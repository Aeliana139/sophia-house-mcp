# Privacy Policy — sophia-house-mcp

_Last updated: 2026-10-05_

This policy describes what the `sophia-house-mcp` server (the "Server") does, what it
collects, and what it does not collect. It applies to anyone who runs or connects to the
Server.

## The short version

The Server is **read-only by construction**. It sends three kinds of requests to the
operator's venue and nothing else. It has no accounts, no tracking, no analytics, no
cookies, no database, and it never stores any user data.

## What the Server does

The Server is a Model Context Protocol (stdio) server. It exposes three tools:

- `verify_settlement` — submits a transaction id to the venue's `/verify/settlement`
  verdict door and returns the verdict.
- `anchor_receipt` — asks the keeper for the mined-status of a transaction id, and (when
  it is a known book anchor) reads the public settlement book.
- `book_status` — reads the public settlement book for a given day.

All network traffic is outbound HTTPS/HTTP from the machine running the Server to the
operator-configured venue base URLs (`SOCSEAL_BASE`, `BOOK_BASE`, `KEEPER_BASE`). By
default those point at loopback (`127.0.0.1`) services on the operator's own machine; set
them to the public front (`https://socseal.xyz`) to read the live venue.

## Data we collect

**None.** The operator does not collect, store, or process any personal data through the
Server, through its tools, or through the venue endpoints the Server reads. There are no
analytics, no telemetry, no cookies, no device fingerprints, and no user profiles.

## Data the Server sends

Only the values you pass to a tool:

- the 64-hex `txid` you submit to `verify_settlement` / `anchor_receipt`, or
- the `day` number you pass to `book_status`.

These are sent to the configured venue solely to answer your request. Nothing else is ever
transmitted: no logs from your machine, no environment details, no files, no keystrokes.

## What the Server never does

- It never spends, signs, seals, or settles anything.
- It never writes to disk on your machine (no persistent state, no cache, no logs).
- It never calls `/settle`, `/seal`, or the signed-transaction relay.
- It does not sell or share anything — there is nothing to sell or share.

## Children's privacy

This service is not directed to children and does not knowingly collect information from
children under the age of 13, consistent with applicable law.

## Third-party links and services

The Server itself contains no third-party code (stdlib only: `json`, `sys`, `urllib`).
The README links to the public venue site for bonus context; those links are informational
and no data is transmitted to them by the Server.

## Changes to this policy

If this policy changes, the change will be committed to this repository with an updated
"Last updated" date.

## Contact

Source repository: https://github.com/Aeliana139/sophia-house-mcp

This policy is provided in plain language on purpose — if a sentence is unclear, the
honest interpretation is the conservative one: the Server collects nothing.