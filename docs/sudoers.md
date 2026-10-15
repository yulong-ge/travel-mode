# Optional sudoers rules

Normal use does not require a custom sudoers file. Travel-mode checks permission
for the exact command with `sudo -n -l <command>` and authenticates on demand in an
interactive terminal. Privileged observations use `sudo -n`.

Use `which pmset`, `which launchctl`, `which mdutil` and `which networksetup` on
the target Mac to verify executable paths. Edit optional rules with
`sudo visudo -f /etc/sudoers.d/travel-mode`; substitute your actual username:

```sudoers
<your-username> ALL=(root) NOPASSWD: /usr/bin/pmset
```

This example grants **every pmset operation**, including disruptive writes and
factory resets. Prefer rules for exact argv, with paired recovery commands using
your captured original values. The required commands depend on hardware and
selected actions; a rule for only the apply command can leave restore blocked.

System launchd operations, Spotlight and Wi-Fi need separate permissions if you
choose those features. Granting unrestricted `launchctl` permits loading arbitrary
system daemons. Do not use `NOPASSWD: ALL`. Do not grant extra permissions merely
to make a demo or ordinary test pass. The real end-to-end harness only permits
its guarded pmset commands and temporary fixture operations.
