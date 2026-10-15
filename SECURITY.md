# Security policy

`travel-mode` can use `sudo` to change power settings, launchd services, Spotlight, Wi-Fi, and Bluetooth. It can also stop or quit applications. Review `travel-mode check` and `--dry-run` output before enabling actions. Keep snapshots protected because they can contain local paths and application names.

Please report vulnerabilities privately through GitHub Security Advisories. Do not include secrets, private paths, or unredacted command output in a public issue.

Supported security fixes target the latest `main` release and the latest version in the `1.1.x` line.
