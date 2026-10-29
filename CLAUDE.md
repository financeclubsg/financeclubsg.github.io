# Stock Pitch Tracker – instructions for Claude

Public site: https://financeclubsg.github.io/ (Finance Club Singapore, an EHL student initiative).
It shows the hypothetical performance of every stock pitch the club posts on LinkedIn.
Maintainer and only person who merges changes: @Nicolasdll05.

## Golden rules

- **Never push to `main` and never merge.** Work on a new branch, then open a pull request. The maintainer reviews and merges.
- **Everything on the site and in the repo is in English**: UI text, commit messages, PR titles and descriptions.
- **No secrets in the repo.** It is public. If a data source needs an API key, say so in the PR and ask the maintainer to add it as a GitHub Actions secret.
- **Keep the disclaimer** ("Hypothetical performance, for educational purposes only. Not investment advice.").
- If a change alters a headline number (club return, benchmark, win rate…), **say so at the top of the PR description**, with the before and after figures.

## How it works

- `data/pitches.csv` is the pitch registry, one row per pitch. A scheduled task on the maintainer's Mac reads the club's LinkedIn page and appends new rows automatically.
  - Columns: `publie_le,entreprise,ticker,bourse,position,prix_pitch,prix_cible,devise,horizon_mois,indice,lien,analyste`.
  - Don't rename, reorder or remove columns. Adding a column is possible, but old rows must stay valid, and the PR must say so: the maintainer then has to update the LinkedIn task.
  - Don't edit existing pitches unless the PR is about fixing wrong data.
- `scripts/update.py` downloads prices from Yahoo Finance (`yfinance`), computes every figure and writes `site/data.json`.
  - Entry price = the share price stated in the pitch. Tracking starts at the last close before the post.
  - Benchmark = S&P 500 Total Return (`^SP500TR`) for every pitch. Each pitch's home market index is shown as a side note.
  - Club portfolio = time-weighted, equal weight across open pitches. Positions are frozen at the end of their horizon.
- `site/index.html` is the whole front end (one file, plain HTML/CSS/JS). It loads `data.json`.
- `site/data.json` is generated and git-ignored. Never commit it.
- `.github/workflows/daily-update.yml` runs the tests, rebuilds `data.json` and deploys to GitHub Pages several times a day and on every push to `main`. Only change it if the PR needs to, and explain why.
- `netlify.toml`: Netlify builds a public preview for every pull request and posts the link as a comment on the PR.

## Working locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover tests   # must pass
.venv/bin/python scripts/update.py            # builds site/data.json
python3 -m http.server 8765 --directory site  # preview at http://localhost:8765 (only on this computer)
```

The localhost preview only works on your own computer. To show your change to others, open the PR and share the Netlify "Deploy Preview" link.

## Before opening the PR

- Tests pass, and `update.py` prints `0 error(s)`.
- The page works in light and dark mode and at phone width (no horizontal scroll), with no errors in the browser console.
- Describe what changed and why in plain English, and add a screenshot for visual changes.
