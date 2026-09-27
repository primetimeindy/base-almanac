# Submission draft

## Project

- **Title:** Almanac: battery policy fault bench
- **Public repository:** https://github.com/primetimeindy/base-almanac
- **Deployed URL:** none. Demonstrate the working local application on screen; hosting is not required by the supplied checklist.
- **Loom:** recording and share URL still required. Target 3 to 4 minutes, within the checklist's 2 to 5 minute range. Use [the live script](demo-script.md).
- **Team names, roles and contacts:** operator confirmation required. No personal contacts are included in this public file.

## Writeup

Battery-control software has to make decisions even when some devices stop answering. Silence does not necessarily mean a battery stopped discharging. Redistributing power too early can double-count capacity, while waiting indefinitely can leave requested delivery unmet. Engineers need a way to inspect those tradeoffs under explicit assumptions before connecting a change to physical equipment.

Almanac is a local, reproducible simulation for that exploration. A user selects an assumed communication-loss area on a map, runs two example policies on the same synthetic fleet, inspects delivery and reserve constraints, and exports a JSON receipt. The historical Hurricane Beryl track provides geographic context only. All battery locations, specifications, targets and failures are fictitious, and neither policy represents Base's production controller.

The comparison includes a no-fault counterfactual because the policies also differ when batteries approach reserve. This prevents presenting the entire delivery difference as recovery from a link failure. A small versioned Python interface also runs a trusted local third policy using current observations, checks returned actions against modeled limits, and identifies the policy version in its receipt.

The intended impact is a more inspectable engineering conversation: a concrete scenario, visible assumptions, reproducible results and an integration example. Commercial need, adoption, savings and production superiority have not been validated. Guaranteed local stops, exact telemetry and instant acknowledgements remain simulation assumptions. No physical devices, controller credentials or public deployment are involved.

## Before submitting

- [ ] Independent review approves the final changes; publish the reviewed branch/PR.
- [ ] Check the public README and final code revision from a clean checkout.
- [ ] Record the core loop live, including a real run, time inspection, no-fault caveat and export. Do not substitute static slides for the app.
- [ ] Verify the Loom link is accessible to judges, with no private tabs or notifications in the capture.
- [ ] Confirm team names, actual roles and approved contact details privately with the operator.
- [ ] Submit once for the team through [the submission form](https://airtable.com/appWQWPtBqDUhCPPj/shrU4GuBeUnMzyrd5).

Checklist reference: [official submission checklist](https://common-scooter-829.notion.site/Submission-Checklist-3e51e636288e80868230ed0fd4a69678). The supplied checklist says “Sept 27, 11 AM CST.” Treat 11 AM Austin local time as the conservative planning cutoff pending organizer clarification about CST versus CDT. This note does not change the organizer's wording.

No submission has been made by creating this file. Recording, team details, publication approval and the final form remain human handoff items.
