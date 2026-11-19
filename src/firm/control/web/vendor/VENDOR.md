# Vendored libraries

Third-party files served by the dashboard, kept in the repo at pinned
versions so the page works on a private network with no CDN (SPEC
"Dashboard stack"). Downloaded from the npm registry; each tarball was
checked against the registry's published SHA-512 integrity before use.
Chosen versions were at least about two weeks old when vendored. A test
(`tests/control/test_dashboard_page.py`) checks the SHA-256 of each file
below, so a change here is always deliberate.

| File | Package | Version | Licence | SHA-256 |
| --- | --- | --- | --- | --- |
| `lightweight-charts.standalone.production.js` | lightweight-charts (TradingView) | 5.2.1 | Apache-2.0 (`lightweight-charts.LICENSE`, attribution: `NOTICE` at the repo root) | `e21cc5caa0226ef30bd8549c50b9ef926615f2a4ee6b4e486353477a55f598cf` |
| `gridstack-all.js` | gridstack | 14.1.0 | MIT (`gridstack.LICENSE`) | `e12690e8e671d752c8007993967830e6a4b8bcfb2787ac85d1100dff765460b4` |
| `gridstack.min.css` | gridstack | 14.1.0 | MIT (`gridstack.LICENSE`) | `74efc82159c5e8d28cb2cebd9f912be9d728bb07fa83896a6b62f4a843207126` |

Vendored 2026-11-19. To upgrade: download the new tarball from
registry.npmjs.org, check its integrity, replace the file, update this table
and the test.
