"""Research and ML desks: Certo-driven agents and the experiment manager.

Agents write hypotheses, strategy code and experiments; the experiment
manager runs budgeted searches (time, trials, GPU memory, tokens, cloud
spend) as a queue. Every trial is counted globally. Outputs are proposals
only (`StrategyCandidate`, `ModelArtifact`). Milestone 3.
"""
