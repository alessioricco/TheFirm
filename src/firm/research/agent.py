"""The researcher agent (M3 plan, M4): one dossier, from idea to one submission.

It acts only through `ResearchTools`, one step per manager tick, and keeps
no state of its own: everything it knows is in the dossier (hypothesis,
code, trials, notes), so a restart picks up where it stopped.

1. **Hypothesis** (model, through the egress gate, private): from the brief,
   the market and the floor brief, a hypothesis and why the edge should
   exist, written before any backtest.
2. **Code** (Certo, sandboxed, local model): a strategy file that follows the
   firm's contract, with its own tests; Certo's verdict is about the code.
   The bytes go to the private store. A failed attempt is noted; after
   `code_attempts` the researcher gives up.
3. **Research backtests** (counted trials, research view only): the model
   proposes parameters, seeing the code and the results so far; each run is a
   trial in the global count. `backtests` runs at most, within the budget.
4. **One submission**: the best parameters by Sharpe on the research view.
   The verifier decides; the researcher never judges its own result.

Model answers are data: the researcher reads them as JSON and checks every
field; anything else is noted and retried, never run. The strategy code is
never imported here: it runs only in the verifier's sandbox.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from firm.research.manager import BudgetSpent, GiveUp, ResearchTools

STRATEGY_FILE = "strategy.py"
TRIAL_NOTE = "trial "  # notes starting with this hold one backtest's JSON
CODE_FAILED_NOTE = "code attempt failed: "
HYPOTHESIS_FAILED_NOTE = "hypothesis answer unreadable: "

CONTRACT = f"""\
Write `{STRATEGY_FILE}` in this folder: one trading strategy for the firm's
backtester, plus `test_strategy.py` with pytest tests for it (synthetic bars
built in the test; no network, no files).

The contract (the backtester refuses anything else):
- Exactly one class. Class attributes: `name` (a short string) and
  `default_params` (a dict of parameter name -> int or float).
- A method `signals(self, bars, params)` returning a pandas Series with one
  value per row of `bars`: the target position, a float in [0, 1]
  (0 = flat, 1 = fully long; no shorting, no leverage). NaN means flat.
- `bars` is a pandas DataFrame with columns open, high, low, close, volume,
  oldest first. `params` is a mapping like `default_params`.
- Causal: the value at row i may use rows 0..i only. Never shift by a
  negative amount, never use future rows, never centre a window.
- Validate `params` and raise ValueError on nonsense values.
- Import only the standard library, numpy and pandas. No network, no files,
  no randomness without a fixed seed.
"""


def _json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in a model's answer, or None."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fence.group(1)] if fence else []
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                candidates.append(text[start : i + 1])
                break
        start = text.find("{", start + 1)
    for c in candidates:
        try:
            value = json.loads(c)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def class_name(code: bytes) -> str:
    """The strategy class's name, read from the source (never imported)."""
    names = re.findall(rb"^class\s+([A-Za-z_][A-Za-z0-9_]*)\s*[:(]", code, re.M)
    if len(names) != 1:
        raise ValueError(f"expected exactly one class, found {len(names)}")
    return names[0].decode()


def trials(tools: ResearchTools) -> list[dict[str, Any]]:
    """The backtests this researcher recorded in its notes, oldest first."""
    out = []
    for n in tools.dossier.notes:
        if n.text.startswith(TRIAL_NOTE):
            try:
                out.append(json.loads(n.text[len(TRIAL_NOTE) :]))
            except ValueError:
                continue
    return out


def _clean_params(value: Any) -> dict[str, int | float] | None:
    if not isinstance(value, Mapping) or not value or len(value) > 20:
        return None
    out: dict[str, int | float] = {}
    for k, v in value.items():
        if not (isinstance(k, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,40}", k)):
            return None
        if isinstance(v, bool) or not isinstance(v, int | float) or v != v:
            return None
        out[k] = v
    return out


class ModelResearcher:
    def __init__(
        self,
        *,
        backtests: int = 6,
        code_attempts: int = 2,
        hypothesis_attempts: int = 2,
        param_attempts: int = 2,
    ) -> None:
        self.backtests, self.code_attempts = backtests, code_attempts
        self.hypothesis_attempts, self.param_attempts = hypothesis_attempts, param_attempts

    async def step(self, tools: ResearchTools) -> None:
        d = tools.dossier
        if d.hypothesis is None:
            return self._hypothesis(tools)
        if not d.code:
            return await self._code(tools)
        done = trials(tools)
        budget_left = d.budget.trials - len(d.trial_ids)
        if len(done) < self.backtests and budget_left > 0:
            return self._backtest(tools, done)
        return self._submit(tools, done)

    # ------------------------------------------------------------ hypothesis
    def _hypothesis(self, tools: ResearchTools) -> None:
        d = tools.dossier
        failed = sum(n.text.startswith(HYPOTHESIS_FAILED_NOTE) for n in d.notes)
        if failed >= self.hypothesis_attempts:
            raise GiveUp(f"no usable hypothesis after {failed} answers")
        prompt = (
            "You are a quantitative researcher. Propose ONE testable trading "
            "hypothesis for a long-only, no-leverage strategy decided on closed bars.\n"
            f"Brief: {d.brief}\nMarket: {d.market}\nInstruments: {', '.join(d.instruments)}\n"
            f"Timeframes: {', '.join(d.timeframes)}\n"
            + (f"Floor brief from the owner: {tools.floor_brief}\n" if tools.floor_brief else "")
            + "Answer with a JSON object only: "
            '{"hypothesis": "<one sentence>", "rationale": "<why the edge should exist>"}'
        )
        answer = _json_object(tools.think([{"role": "user", "content": prompt}])) or {}
        h, r = answer.get("hypothesis"), answer.get("rationale")
        if not (isinstance(h, str) and isinstance(r, str) and h.strip() and r.strip()):
            tools.note(f"{HYPOTHESIS_FAILED_NOTE}no hypothesis and rationale strings")
            return
        tools.write_hypothesis(h.strip()[:1000], r.strip()[:2000])

    # ------------------------------------------------------------------ code
    async def _code(self, tools: ResearchTools) -> None:
        d = tools.dossier
        failed = sum(n.text.startswith(CODE_FAILED_NOTE) for n in d.notes)
        if failed >= self.code_attempts:
            raise GiveUp(f"no working strategy code after {failed} Certo tasks")
        task = (
            f"{CONTRACT}\nThe strategy tests this hypothesis: {d.hypothesis}\n"
            f"Why: {d.rationale}\nIt trades {', '.join(d.instruments)} on "
            f"{', '.join(d.timeframes)} bars."
        )
        result = await tools.write_code(task)
        why = None
        if not result.passed or result.code is None:
            why = f"Certo verdict {result.verdict}: {result.note}"[:300]
        else:
            try:
                class_name(result.code)
            except ValueError as exc:
                why = str(exc)
        if why:
            tools.note(CODE_FAILED_NOTE + why)
            return
        assert result.code is not None
        tools.save_code(result.code, stem="strategy")

    # ------------------------------------------------------------- backtests
    def _where(self, tools: ResearchTools) -> dict[str, str]:
        d = tools.dossier
        instrument, tf = d.instruments[0], d.timeframes[0]
        found = tools.datasets(instrument, tf)
        if not found:
            raise GiveUp(f"no dataset holds {instrument} {tf} bars")
        version, venue, _rows = found[0]
        return {"dataset_version": version, "venue": venue, "instrument": instrument, "tf": tf}

    def _backtest(self, tools: ResearchTools, done: list[dict[str, Any]]) -> None:
        d = tools.dossier
        ref = d.code[-1]
        code = tools.read_code(ref.file)
        params = None
        for _ in range(self.param_attempts):
            prompt = (
                f"Hypothesis: {d.hypothesis}\nStrategy code:\n```python\n"
                f"{code.decode(errors='replace')[:12000]}\n```\n"
                f"Backtests so far (params and research-view metrics): "
                f"{json.dumps([{k: t[k] for k in ('params', 'metrics')} for t in done])}\n"
                "Propose the next parameter set to try: a different one, informed by "
                "the results; keep it plausible, not curve-fit. Answer with a JSON "
                'object only: {"params": {<name>: <number>, ...}}'
            )
            answer = _json_object(tools.think([{"role": "user", "content": prompt}])) or {}
            params = _clean_params(answer.get("params"))
            if params is not None and all(t["params"] != params for t in done):
                break
            params = None
        if params is None:
            if done:  # nothing new to try: submit the best so far
                return self._submit(tools, done)
            raise GiveUp("the model proposed no usable parameters")
        where = self._where(tools)
        try:
            r = tools.backtest(ref.file, class_name(code), params, **where)
        except BudgetSpent:
            raise
        except Exception as exc:  # the strategy failed on real bars: a trial, noted
            tools.note(
                TRIAL_NOTE
                + json.dumps({"params": params, "metrics": None, "error": str(exc)[:300]})
            )
            return
        metrics = {
            k: float(v)
            for k, v in dict(r.get("metrics") or {}).items()
            if isinstance(v, int | float)
        }
        tools.note(
            TRIAL_NOTE
            + json.dumps({"params": params, "metrics": metrics, "trial_id": r.get("trial_id")})
        )

    # ---------------------------------------------------------------- submit
    def _submit(self, tools: ResearchTools, done: list[dict[str, Any]]) -> None:
        scored = [t for t in done if t.get("metrics") and "sharpe" in t["metrics"]]
        if not scored:
            raise GiveUp("no backtest produced metrics: nothing to submit")
        best = max(scored, key=lambda t: t["metrics"]["sharpe"])
        ref = tools.dossier.code[-1]
        code = tools.read_code(ref.file)
        tools.submit_candidate(ref.file, class_name(code), best["params"], **self._where(tools))
