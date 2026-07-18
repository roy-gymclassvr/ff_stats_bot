// pm2 config: run the stats bot once, nightly at 11pm America/Chicago.
// The script is a one-shot (connects, processes, exits), so autorestart is OFF
// and cron_restart re-launches it on schedule. The pm2 daemon inherits the
// machine timezone (America/Chicago), so "0 23 * * *" == 11pm Central.
module.exports = {
  apps: [{
    name: "ff-stats",
    script: "main.py",
    interpreter: "/Users/roy/WebstormProjects/gym_class/ff_tournament_stats/.venv/bin/python",
    cwd: "/Users/roy/WebstormProjects/gym_class/ff_tournament_stats",
    autorestart: false,          // one-shot: run to completion, then stay stopped
    cron_restart: "0 23 * * *",  // 11:00pm daily, daemon TZ = America/Chicago
    time: true,                  // prefix log lines with timestamps
  }],
};
