# Vendored clusterdOS config chart

Source: [clusterdOS v0.4.8](https://gitlab.com/aranya-tech/public/clusterdos/-/tree/v0.4.8/gitapps/config),
commit `92a191cdb1ecda83c25d9d218fce3dd3f7b2ba1c`.

Modified on 2026-11-06: `Chart.yaml` changes only `apiVersion: v3` to `v2`.
The upstream value is unsupported by Helm and prevents Argo CD from rendering
`clusterdos-config`. `values.yaml` and the ConfigMap template are unchanged.

This chart is distributed under the upstream GNU GPL version 3; see [LICENSE](LICENSE).
It is provided without warranty. No other upstream charts or release pins are changed.

`install.yaml` redirects only the `config` gitapp to this directory in the
submission repository at a pinned commit. Remove the override only after verifying
that a later upstream release fixes and renders the chart.
