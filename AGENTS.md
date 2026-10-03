# Repository boundaries

- Keep UI and backend changes in separate branches and commits.
- UI code belongs in `preview/`; the existing UI work is on `map-ui`.
- Backend code belongs in `pipeline/` and `fetch_pipeline/`.
- Shared backend test configuration, CI workflows, and verification scripts may live at the repository root, in `.github/`, and in `scripts/`.
- Do not move backend logic into UI files or include UI changes in a backend commit.
- Follow an explicit user request when it changes these boundaries.
