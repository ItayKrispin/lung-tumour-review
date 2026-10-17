# Out-of-box proposal results

Each number below is an unedited MedSAM2 outline. The click is the center of the expert tumor, which simulates Daniel having already found it. The outline was not painted and was not accepted. These rows are not in his sheet.

Scored visits: 57. Failed visits: 2.

## Agreement

- Median overlap with the expert outline: 47%.
- Median absolute volume error: 19,254 mm³ (194.7%).
- Median absolute mean-diameter error: 30.0 mm.

## Same-day wobble

Across 27 patients with both scans, the median absolute change in the tool's volume was 12,071 mm³, and the median absolute change in mean diameter was 5.2 mm. The tumor has not changed between these scans, so that change is wobble.

## Time

The first click took 25.6 seconds, including the model load. The median of the later clicks was 21.4 seconds. Those seconds are the model, not a person.

## Time saved

This is an estimate, not Daniel's minutes and not a stopwatch on all 59 visits. Itay stood in for Daniel and corrected against the expert outline, because Daniel cannot run the page in this setup.

The expert outline was on screen, so the 10–20 minutes Daniel spends finding the nodule are not in these times. On a new scan that step comes back, and this estimate does not include it.

Correction minutes are a scenario tied to the measured overlap of the unedited proposal:

- Overlap 70% or better: 4 minutes. 18 visits.
- Overlap from 40% up to 70%: 8 minutes. 12 visits.
- Overlap under 40%, or no outline: 15 minutes, the bottom of his 15–25 minute hand outline. 29 visits. These are not faster.

The sheet step in the scenario is 2 minutes. His sheet step is 3–5 minutes. Estimated minutes are correction plus that sheet step.

The median correction in the scenario is 8 minutes, against his 15–25 minute outline. The median estimated time, including the sheet step, is 10 minutes. That is not a claim about the whole week. The batch above still measures agreement. The per-visit rows are in `itay_time_estimate.csv`.

## Failure gallery

The worst five visits by overlap. A low overlap, a large volume error, or many extra slices means Daniel would still have to paint.

- RIDER-9763310455 retest: overlap 0%, volume error 159,592 mm³ (16,577.4%), mean-diameter error 101.5 mm, 118 extra slices.
- RIDER-2655999012 test: overlap 0%, volume error 1,789,277 mm³ (60,305.6%), mean-diameter error 130.4 mm, 133 extra slices.
- RIDER-9763310455 test: overlap 1%, volume error 318,240 mm³ (32,606.4%), mean-diameter error 109.8 mm, 107 extra slices.
- RIDER-2655999012 retest: overlap 1%, volume error 810,695 mm³ (29,233.7%), mean-diameter error 116.4 mm, 79 extra slices.
- RIDER-3023568408 test: overlap 3%, volume error 445,742 mm³ (7,634.9%), mean-diameter error 71.6 mm, 75 extra slices.

## The pair already shown to the lab

On RIDER-1129164940 the click was made by hand in the viewer, not at the measured center used above. The test outline overlapped the expert by 64% (volume 135,253 mm³, mean diameter 84.2 mm). The retest overlapped by 60% (volume 130,610 mm³, mean diameter 94.2 mm). The volume barely changed. The mean diameter moved by about 10 mm. Those scans were taken the same day, so that gap is measurement wobble. Outlining that tumor by hand takes Daniel 15–25 minutes. These batch numbers measure agreement. They are not his minutes.

## What this does not show

The click is given. The report does not show that the tool can find every nodule. A proposal that continues onto slices the expert left empty is counted in extra slices.

Visits that did not produce an outline:

- RIDER-2357766186 retest: Click the nodule, not the air around it.
- RIDER-5195703382 test: Click the nodule, not the air around it.
