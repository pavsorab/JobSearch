# profiles/

Your personal data lives here, one folder per person: profile.json, candidate.md,
skills.json, master-cv.tex, notes, state (seen jobs, applied list, Overleaf mapping),
runs and results. Everything in this folder except this README is git-ignored.

Create one with `/setup_profile <resume>` or `python3 jptools.py new-profile <name>`,
then set `JOBSEARCH_PROFILE=<name>` in `.env`.

To move a profile to another computer, copy its folder (and your `.env`) directly:
AirDrop, a USB stick, or a private repository. Never through this public repository.
