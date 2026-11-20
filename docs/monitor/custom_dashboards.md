# Custom Grafana Dashboards

RL-Insight can copy Dashboard files from additional directories into its existing Grafana runtime directory while keeping the bundled dashboards.

## Add a dashboard directory

Pass the dashboard directory when starting the stack:

```bash
rl-insight server start --extra-dashboard-dir ./dashboards
```

The path is resolved from the current working directory and must be visible on the machine that runs the RL-Insight server. Put all custom Dashboard files under this directory; subdirectories are supported.

## Copy behavior

RL-Insight first copies its bundled dashboards to `runtime/dashboards`, then copies the extra directory into the same location while preserving relative paths.

Before copying the extra directory, RL-Insight checks whether an extra `.json` file would use the same relative path as a bundled dashboard. A conflict stops startup before any extra files are copied.

RL-Insight does not parse or validate Dashboard JSON during this copy. Grafana remains responsible for accepting the file and reporting invalid Dashboard content.

Source directories are copied when the server stack starts. Restart the stack after adding, updating, or removing source files. A filesystem copy error stops startup; RL-Insight does not restore the previous runtime directory.

The external JSON file remains the maintenance source. Grafana UI changes are not written back to it and can be replaced after a later restart.
