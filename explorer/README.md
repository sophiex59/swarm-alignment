# Explorer pages

Plain HTML pages; open them straight from disk in any browser.

- `round_flow.html`: an illustrative walkthrough of one Discovery round (who acts in each lap, what stays private, what reaches the board).
- `results.html`: the template for the results explorer. Build the real page from your local game logs with:

  ```sh
  uv run python -m swarm_alignment.export_results
  ```

  This writes `results/explorer/index.html` with the run summaries embedded (and `data.json` beside it). `results/` is not in git, so each person builds the page from the logs they have. Runs are listed in `RUNS` in `src/swarm_alignment/export_results.py`.
