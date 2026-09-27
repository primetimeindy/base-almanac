# Live demo narration

Target: 3 to 4 minutes. Record the working local app in Loom, not a slide-only walkthrough. Keep the browser and a prepared terminal visible; hide private tabs and notifications. Read the actual on-screen values rather than memorizing numbers. Timing is a recording target, not proof that a video exists.

## 0:00 to 0:30 | The question

**Show:** the main page and its simulation-only label.

“Almanac asks a narrow engineering question: when some batteries stop answering, how should the others respond? A silent link does not mean the battery has stopped discharging. That matters when you decide how much power to ask from the rest of a fleet.

“This is a local simulation, not Base's controller, and there are no physical devices connected. I can compare example policies, inspect the assumptions, and keep the result as a reproducible receipt.”

## 0:30 to 1:10 | Map and assumed failure

**Do:** select Houston-side cluster. Point to the selected-device count and rectangle, then click **Compare example policies**.

“The blue line is the official historical Hurricane Beryl best track. It is context, not an outage map. The dots are fictitious battery positions. I choose this rectangle and the simulator disconnects those communication links from second sixty to second three hundred sixty.

“Both policies get the same initial batteries and fault schedule. Fixed shares keeps each battery's original share. Redistribution uses spare capacity from batteries that still have fresh readings. Neither example is claimed to be optimal.”

## 1:10 to 1:55 | Constraints and the result

**Do:** inspect second 60, second 90 and second 360 with the slider. Read delivered power and unknown-output budget from the table. Point to the visible assumptions.

“At the first silent interval, the last command might still be running. The policy counts that unknown output against its budget instead of immediately adding the same power somewhere else. Once the assumed stop bound has elapsed, it can reallocate within fresh capacity. When readings return, it can use those devices again.

“This behavior depends on guaranteed local stops, instant acknowledgements and exact telemetry. The simulated plant protects backup reserve for both policies. Those are assumptions to validate, not proof of real-world safety.”

## 1:55 to 2:35 | Challenge the headline

**Do:** point to the full-run difference and no-fault comparison. Select **Empty selection**, run, then **All-device stress**, run and inspect second 100.

“The full-run difference is not all recovery from the selected link failure. Even without any link failure, ten batteries start near reserve, and the policies handle redistribution differently. The receipt includes the no-fault comparison. Subtracting those differences still does not isolate a clean fault effect because reserve use and faults interact.

“With every device unavailable and the commands expired, redistribution cannot invent capacity. This is one synthetic scenario at a time, not a performance study or commercial savings estimate.”

## 2:35 to 3:20 | Integration and receipt

**Do:** export the browser result. Show the downloaded JSON or its receipt hash. In a terminal run the command below, then show the policy identity and candidate metrics.

```sh
PYTHONPATH=src:. python -m almanac.controller_contract \
  --policy examples.priority_policy:Policy --area core > policy-receipt.json
python -c "import json; r=json.load(open('policy-receipt.json')); print(r['policy']); print(r['candidate']['metrics'])"
```

“The browser exports the exact displayed response. For another policy, there is a versioned local Python interface. This third example allocates in ID order. It receives only current fresh observations and conservative bounds. Returned powers are checked for valid IDs, finite values, per-device limits, available energy and the remaining fleet budget.

“The receipt identifies the policy and version. Imports are trusted local code, not sandboxed uploads, and there is no physical transport adapter.”

## 3:20 to 3:45 | Close with scope

**Show:** the repository quickstart, test command and limitations.

“The deliverable is a working map, deterministic simulation, readable comparison, export and a tested integration example. It runs without keys or model calls. The next step would be to validate the assumptions with engineers and test realistic delayed or noisy telemetry, not claim this toy model is production-ready.”

## Recording checks

- Show at least one fresh run completing, not only a preloaded result.
- Keep observed historical data separate from simulated faults and batteries.
- If a run fails or is busy, show the error and retry. Do not narrate a result that did not run.
- Keep the final video between 2 and 5 minutes; aim for 3 to 4.
- Confirm export and Loom playback before submitting the share link.
