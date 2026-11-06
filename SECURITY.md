# Security

If you believe you have found a security vulnerability in frostyard/lab, report
it privately.

## Supported versions

lab is the GitOps source of the frostyard QA pipeline. It has no releases:
Argo CD reconciles the cluster from `main`, and GitHub Pages serves the QA
dashboard from `main`.

| What | Gets security fixes |
| --- | --- |
| The `main` branch | Yes: a fix merges to `main`, Argo CD syncs it to the cluster, and the Pages workflow republishes the dashboard |
| Older commits and other branches | No |

The images under test are not part of this repository. Report a
vulnerability in a snosi image to
[frostyard/snosi](https://github.com/frostyard/snosi/security), and one in
the BDD suites to
[frostyard/testsuite](https://github.com/frostyard/testsuite/security).

## Reporting a vulnerability

- Do **not** report security vulnerabilities through public GitHub issues.
- Use GitHub private vulnerability reporting for this repository:
  <https://github.com/frostyard/lab/security> (the **Report a
  vulnerability** button on the Security tab).
- If that button is not available, email the project maintainer at
  [bketelsen@gmail.com](mailto:bketelsen@gmail.com). Use a subject such as
  `[lab security] Brief description`.

The **New draft security advisory** page is a maintainer workflow and is not a
fallback reporting channel for people without repository write access.

## Guidelines

- Include the affected file, workflow or lane, the commit, the impact, and
  complete reproduction steps when possible.
- Do not send credentials, kubeconfigs, tokens, or private host details.
- Do not send scripts that download and run binaries.
- We prioritize reports that demonstrate realistic impact.
- We do not offer financial rewards. We are happy to acknowledge your research
  publicly when possible.

The security boundaries for AI agents and automated contributors are in
[docs/SECURITY-AI.md](docs/SECURITY-AI.md).
