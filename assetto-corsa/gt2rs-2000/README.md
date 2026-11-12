# GT2 RS track build: the "2500hp MONSTER"

Car: the mod `porsche_911_gt2_rs_wss_2000` (a 991 GT2 RS). The mod's files are not in
this repository; `tune.py` makes the edits below to an installed copy and keeps the
originals beside them as `<name>.orig-before-tune`. Run it with the game closed.

| Step | Change | Best lap, Road America |
|---|---|---|
| As downloaded (Cup 2 tyres) | | 2:19.805 |
| Brakes | brake torque 4150 -> 6000 N·m, brake pedal recalibrated | 2:10.853 |
| Slicks | third tyre compound, grip about 1.76-1.82 (Cup 2: 1.58) | 2:03.624 |
| Downforce | front, rear and diffuser lift x3, springs 68/79 -> 110/150 kN/m | 2:00.510 |
| Power | about 1,880 hp as first driven (see below), stronger clutch, longer 7th | 2:00.844, 260 mph |
| Redline 9,000 rpm | maps corrected to 2,000 and 2,520 hp | 1:54.338, 259 mph |
| Redline 12,000 rpm | switched while driving; full power held from 9,000 to 12,000 | 1:57.546, 264 mph (see below) |

What the recorded laps showed along the way:

- Brakes: full-pedal deceleration above 124 mph went from 1.77 g to 2.53 g over the build.
- Downforce, from tyre loads: about 1,090 kg at 153 mph and 2,050 kg at 202 mph, roughly 26% of it on the front axle.
  Cornering above 150 mph rose from 1.46 g to 2.68 g. Top speed fell from 224 to 205 mph.
- Power: as downloaded the car made about 1,090 hp, not 2,000. Two controller files held each turbo at 0.9 bar.
  With them out of the way the gauge read 4.71 bar and 2,491 hp on the high map, as calculated. The default map
  came out at 1,881 hp instead of 2,000: the game keeps the boost setting in steps of 10%, and 76% was read as 70%.
  The wastegate is now sized so both maps sit on a step: 60% is 2,000 hp, 80% is 2,520 hp.
- With about 1,880 hp the car reached 260 mph where it had managed 205, and traction control was busy up to 120 mph
  instead of 90. The lap was no quicker than with 1,090 hp: the time is now in getting the power down.
- Two rev limits: the car carries a small script (`data/script.lua`, written by `tune.py`) that switches between
  9,000 and 12,000 rpm while driving, using the patch's "Extra F" switch or the REV switch in the gauge app. At 12,000
  the final drive is a third shorter, so each gear reaches the same speed, and full power is held from 9,000 to 12,000.
- Redline: `--redline` moves the rev limit (9,000 by default; the mod's is 7,200). The torque curve is stretched up
  the rev range and the final drive shortened by the same factor, so power and the speed in each gear are unchanged
  and only the revs rise.

- On the 12,000 rpm lap, one corner (Turn 2, taken at 24 mph instead of 51) cost 4.2 s. The other eleven corners
  together were 1.0 s quicker than on the 1:54.338 lap, and its middle sector (42.970) is the best recorded.
  Best sectors added together: 1:53.374.
- Where the time still is: traction control cuts power for 13 to 16 s a lap at full throttle, nearly all of it in
  gears 1 to 4; 7th gear runs into the limiter at 264 mph; and only about 28% of the load is on the front axle above 200 mph.
- `tune.py` also renames the car in the game's menus to "2500hp MONSTER" with matching figures. The folder name keeps
  its `_2000`, because setups, saved laps and the sound file are all tied to it.

- Traction package (not yet driven): all-wheel drive with 35% of the torque to the front through a centre differential
  that locks under power; boost by gear (55%, 70%, 85% and 95% of the wastegate in 1st to 4th, full from 5th), done by
  the script in the car; 7th lengthened again (0.55 to 0.50); and 35% more front downforce. `--rwd` and `--flat-boost`
  leave the first two out.

The brake pedal change is in the game's own `cfg/controls.ini`: `[BRAKES] MAX=0.88`, because a stiff brake spring
kept the pedal from ever reading 100%.

Switching between the two maps, and between engine sounds, is done in the car by the
[Boost and Power app](../apps/rig_power).
