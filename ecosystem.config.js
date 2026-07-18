// pm2 config: run the stats bot once, nightly at 11pm America/Chicago.
// The script is a one-shot (connects, processes, exits), so autorestart is OFF
// and cron_restart re-launches it on schedule.
//
// Paths are derived from this file's location (__dirname), so it works on any
// machine as long as a .venv exists in the repo dir (python3 -m venv .venv).
//
// TIMEZONE: cron_restart fires in the pm2 DAEMON's timezone, not this env.
// On the server set the system tz once so 11pm == 11pm Central, DST-aware:
//   sudo timedatectl set-timezone America/Chicago
const path = require("path");
module.exports = {
  apps: [{
    name: "ff-stats",
    script: "main.py",
    interpreter: path.join(__dirname, ".venv/bin/python"),
    cwd: __dirname,
    autorestart: false,          // one-shot: run to completion, then stay stopped
    cron_restart: "0 23 * * *",  // 11:00pm daily, in the daemon's timezone
    time: true,                  // prefix log lines with timestamps
  }],
};
