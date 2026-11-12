-- Drives the car script with a stub car: replays paddle pulls at given speeds and reports what got through.
local path = arg[1]
local phys = { gear = 8, gearDown = false }
car = { isAIControlled = false, extraF = true, speedKmh = 0, wheels = { [2] = { tyreRadius = 0.352 } } }
script = {}
local limitSet, finalSet
ac = { accessCarPhysics = function() return phys end, setEngineRPMLimit = function(v) limitSet = v end,
       setGearsFinalRatio = function(v) finalSet = v end, setTurboWastegate = function() end }
dofile(path)
local RAT = { 3.75, 2.38, 1.72, 1.30, 1.02, 0.82, 0.5 }
local function revs(gear, mph) return mph * 0.44704 / 0.352 * 9.5493 * RAT[gear] * finalSet end
local dt = 1 / 333
local function step(n, down) for _ = 1, n do phys.gearDown = down; script.update(dt) end end
local function pull(mph, shiftMs, heldSteps)
  -- one pull: paddle down for heldSteps physics steps; if the script leaves it set on the first step the game shifts
  car.speedKmh = mph * 1.609344
  phys.gearDown = true; script.update(dt)
  local passed = phys.gearDown
  local from = phys.gear - 1
  for _ = 2, heldSteps or 10 do phys.gearDown = true; script.update(dt); if phys.gearDown and not passed then error('let through late') end end
  step(3, false)
  return passed, from
end
local function run(title, mode, startGear, pulls, lag)
  car.extraF = mode == 12000
  phys.gear = startGear + 1; step(400, false)
  print(title .. string.format('  (limit %d, final %.3f)', limitSet, finalSet))
  local pendingGear, pendingSteps = nil, 0
  for _, p in ipairs(pulls) do
    local mph, gapMs = p[1], p[2]
    -- time passes between pulls; a shift the game accepted completes after `lag` ms
    local steps = math.floor(gapMs / 1000 / dt)
    for _ = 1, steps do
      if pendingGear then pendingSteps = pendingSteps - 1; if pendingSteps <= 0 then phys.gear = pendingGear + 1; pendingGear = nil end end
      phys.gearDown = false; script.update(dt)
    end
    local shown = phys.gear - 1
    local passed = pull(mph)
    local goingTo = (pendingGear or shown) - 1
    if goingTo < 1 then break end
    if passed then
      print(string.format('   %3d mph  pull: ALLOWED  %d -> %d   lands at %5.0f rpm', mph, pendingGear or shown, goingTo, revs(goingTo, mph)))
      pendingGear, pendingSteps = goingTo, math.floor(lag / 1000 / dt)
    else
      print(string.format('   %3d mph  pull: refused  (stays in %d; %d would be %5.0f rpm)', mph, pendingGear or shown, goingTo, revs(goingTo, mph)))
    end
  end
end
-- the burst that killed the engine: six pulls 0.21 s apart from 7th, 199 -> 142 mph, game shows the old gear until the shift lands
run('fatal burst, 12K', 12000, 7, { {199, 500}, {187, 210}, {170, 210}, {168, 210}, {160, 210}, {153, 210}, {142, 210} }, 180)
-- normal braking into a slow corner, 12K: pulls as the car slows
run('ordinary braking, 12K', 12000, 7, { {250, 500}, {165, 600}, {130, 500}, {100, 500}, {76, 500}, {55, 500}, {34, 600} }, 180)
run('ordinary braking, 9K', 9000, 7, { {180, 500}, {125, 600}, {98, 500}, {75, 500}, {57, 500}, {41, 500}, {26, 600} }, 180)
-- paddle held down the whole time: must not shift on its own later
car.extraF = true; phys.gear = 8; step(400, false); car.speedKmh = 250 * 1.609344
phys.gearDown = true; script.update(dt); local first = phys.gearDown
local leaked = false
for i = 1, 2000 do car.speedKmh = (250 - i * 0.1) * 1.609344; phys.gearDown = true; script.update(dt); if phys.gearDown then leaked = true end end
print('held paddle from 250 mph in 7th: first step ' .. (first and 'allowed' or 'refused') .. ', later ' .. (leaked and 'LEAKED' or 'stays refused until released'))
