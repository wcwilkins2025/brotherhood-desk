# Brotherhood Desk

A private daily desk for one member of The Brotherhood (Skool). Each morning a local job pulls new posts and comments, sorts them into forums, drafts replies for approval, and publishes an encrypted data file to this repo's GitHub Pages site. The site asks for a passphrase and decrypts in the browser. Nothing here posts to Skool; every reply is copied and pasted by hand.

## Pieces
- `collector/collect.py`: Playwright with a saved login profile. Feed pages, comments (first 25 and newest 25 per post), leaderboard, daily count snapshots.
- `analyst/analyze.py`: Claude Code headless. Forum labels, reply scores, drafts in the owner's voice (`analyst/voice.md`).
- `analyst/build_desk.py`: merges everything into `data/desk.json`.
- `publish.py`: AES-GCM encrypts `desk.json` with `DESK_PASSPHRASE` from `.env`, copies the app to `docs/`, commits and pushes.
- `run_daily.bat`: the 6 a.m. scheduled task.
- `app/index.html`: the page. Tabs: Today, Reply queue, My threads, Forums, Members (posting trend), Leaderboards, Charts, Experiment.

## Not in git
`.profile/` (browser login), `.env` (passphrase, token), `data/` (member content in plain text).
