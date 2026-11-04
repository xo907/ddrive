"""`python -m discorddrive` (a launcher from before the rename): says so and ends cleanly (exit 0), so a leftover
discorddrive.service stops instead of restarting forever. `ddrive mount` removes such a unit (ddrive/service.py)."""
import sys

print("DiscordDrive was renamed to DDrive: run `python3 -m ddrive` (or ./run.sh). This launcher does nothing.",
      file=sys.stderr)
sys.exit(0)
