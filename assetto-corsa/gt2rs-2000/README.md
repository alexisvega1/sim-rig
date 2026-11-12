# GT2 RS "2000 hp" track build

Car: the mod `porsche_911_gt2_rs_wss_2000` (a 991 GT2 RS). The mod's files are not in
this repository; `tune.py` makes the edits below to an installed copy and keeps the
originals beside them as `<name>.orig-before-tune`. Run it with the game closed.

| Step | Change | Best lap, Road America |
|---|---|---|
| As downloaded (Cup 2 tyres) | | 2:19.805 |
| Brakes | brake torque 4150 -> 6000 N·m, brake pedal recalibrated | 2:10.853 |
| Slicks | third tyre compound, grip about 1.76-1.82 (Cup 2: 1.58) | 2:03.624 |
| Downforce | front, rear and diffuser lift x3, springs 68/79 -> 110/150 kN/m | 2:00.510 |
| Power | 2,005 hp default, 2,500 hp map, stronger clutch, longer 7th | not yet driven |

What the recorded laps showed along the way:

- Brakes: full-pedal deceleration above 124 mph went from 1.77 g to 2.53 g over the build.
- Downforce, from tyre loads: about 1,090 kg at 153 mph and 2,050 kg at 202 mph, roughly 26% of it on the front axle.
  Cornering above 150 mph rose from 1.46 g to 2.68 g. Top speed fell from 224 to 205 mph.
- Power: as downloaded the car made about 1,090 hp, not 2,000. Two controller files held each turbo at 0.9 bar.
  With them out of the way, 3.58 bar total gives 2,005 hp at 7,200 rpm and 4.71 bar gives 2,500 hp.

The brake pedal change is in the game's own `cfg/controls.ini`: `[BRAKES] MAX=0.88`, because a stiff brake spring
kept the pedal from ever reading 100%.

Switching between the two maps, and between engine sounds, is done in the car by the
[Boost and Power app](../apps/rig_power).
