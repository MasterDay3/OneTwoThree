# Changelog

All notable changes to this project, newest first. Entries are grouped by UTC date.

## 2026-09-29

### Lab 2: custom domains, one-command deploys and automatic deploys from main — 13:31 UTC
**Summary:** The app can now live on your own web addresses (app. and api.), and every change merged to main is checked and published automatically, without storing any AWS passwords.
**Details:** Adds PROJECT.md (repository contract), an HTTPS custom domain for the API, `make deploy-backend`/`deploy-frontend` shared by people and CI, images tagged by commit, one-command rollback, a GitHub deploy role limited to this fork's main branch, a deploy workflow, a fix so a local .env can no longer override CI credentials, 156 infra tests and written answers to the lab questions.
