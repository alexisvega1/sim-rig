-- Boost and Power: a boost dial, live horsepower and torque, and the power curve
-- the car actually makes, filled in as you drive. On cars listed in CARS below it
-- also carries three switches: the engine map (how much boost), the rev limit and the engine sound.
-- A second window, Wheel knobs, draws the wheel's turning controls, shows what each one does, and
-- lets any of them be given one of those switches.
--
-- Engine output is worked out the way Assetto Corsa itself does it: the car's
-- torque curve (data/power.lut, in N·m) multiplied by (1 + boost), then by how
-- far the throttle is open. Power follows from torque and revs. The curve only
-- records full-throttle moments, so each pull through the revs fills in more.

local NM_TO_LBFT = 0.7375621
local BAR_TO_PSI = 14.504
local ROOT = ac.getFolder(ac.FolderID.Root)
local OUT = ROOT .. '/rig_power_telem.txt'

-- Cars with switches. A map's level is the share of the car's highest boost setting
-- (engine.ini WASTEGATE) the turbos are allowed to reach; the game keeps it in steps of 0.1.
-- A sound names the installed car whose engine sounds to borrow; turbo = true also borrows
-- its turbo whistle, and turboFrom takes the turbo sounds from a different car instead (turboLoud
-- sets how loud); top is the highest revs that sound was recorded for. revs lists the rev
-- limits the car's own script (data/script.lua) offers; the second is selected by turning on
-- the Custom Shaders Patch extra switch numbered revSwitch (0 = A ... 5 = F).
local CARS = {
  porsche_911_gt2_rs_wss_2000 = {
    maps = {
      { name = '2,000', level = 0.60 },
      { name = '2,500', level = 0.80 },
    },
    revs = { { name = '9K', rpm = 9000 }, { name = '12K', rpm = 12000 } },
    revSwitch = 5,
    sounds = {
      { name = '935 TURBO', car = 'ks_porsche_935_78_moby_dick', turbo = true, top = 9300 },
      { name = '911 RSR', car = 'ks_porsche_911_rsr_2017', turbo = false, top = 9000 },
      { name = 'GT1 TURBO', car = 'ks_porsche_911_gt1', turbo = true, top = 8000 },
      { name = '917/30', car = 'ks_porsche_917_30', turbo = true, top = 8500 },
      { name = 'STOCK', top = 7200 },
      { name = 'RSR + TURBO', car = 'ks_porsche_911_rsr_2017', turboFrom = 'ks_porsche_911_gt1', turboLoud = 2.0, top = 9000 },
    },
  },
}
-- How much louder than the sound's own level: 1 = as recorded.
local LOUD = { engine = 1.2, backfire = 1.5, turbo = 1.5 }

local curve = nil            -- the car's torque curve without boost
local curveTried = false
local peak = { boost = 0, hp = 0, tq = 0, hpRpm = 0, tqRpm = 0 }
local bins = {}              -- [rpm/100] = best full-throttle torque (lb-ft) seen there
local now = { rpm = 0, boost = 0, gas = 0, tq = 0, hp = 0, limiter = 8000, gear = 0, mph = 0, gx = 0, gz = 0, brake = 0, steer = 0 }
local acc, seq = 0, 0
-- g-force as the cluster shows it: smoothed values, a short trail, and the most seen in each direction
local G = { x = 0, z = 0, trail = {}, timer = 0, up = 0, down = 0, left = 0, right = 0 }

-- The wheel's turning controls, laid out as on a MOZA KS Pro: four thumb wheels and five knobs. x and y place each
-- on the Wheel knobs panel (0..1). up and dn are the buttons one click right and left sends; the thumb wheels start
-- with the numbers the game's own controls were set up with, and any of them can be taught again on the panel.
local SLOTS = {
  { id = 'tc', name = 'TC', thumb = true, x = 0.10, y = 0.16, up = 45, dn = 44 },
  { id = 'dif', name = 'DIFF', thumb = true, x = 0.10, y = 0.50, up = 49, dn = 48 },
  { id = 'abs', name = 'ABS', thumb = true, x = 0.90, y = 0.16, up = 47, dn = 46 },
  { id = 'bb', name = 'BB', thumb = true, x = 0.90, y = 0.50, up = 51, dn = 50 },
  { id = 'k1', name = 'PINK', color = 'D946EF', x = 0.29, y = 0.40 },
  { id = 'k2', name = 'GREEN', color = '2DD4BF', x = 0.39, y = 0.80 },
  { id = 'kc', name = 'CENTRE', color = 'A78BFA', x = 0.50, y = 0.36 },
  { id = 'k3', name = 'BLUE', color = '4C9BE8', x = 0.61, y = 0.80 },
  { id = 'k4', name = 'ORANGE', color = 'F2703B', x = 0.71, y = 0.40 },
}
local FNS = { 'MAP', 'REV', 'SOUND' }                   -- what a knob can be given to do here (0 = nothing)
-- The game's own bindings worth naming on the panel: section for one way, section for the other, what it is.
local GAME = {
  { 'TCUP', 'TCDN', 'traction control' }, { 'ABSUP', 'ABSDN', 'ABS' }, { 'TURBOUP', 'TURBODN', 'turbo boost' },
  { 'BALANCEUP', 'BALANCEDN', 'brake bias' }, { 'ENGINE_BRAKE_UP', 'ENGINE_BRAKE_DN', 'engine brake' },
  { '__EXT_FFB_INCREASE', '__EXT_FFB_DECREASE', 'force feedback' }, { '__EXT_ENGINEMAP_UP', '__EXT_ENGINEMAP_DOWN', 'engine map' },
  { '__EXT_TC2_UP', '__EXT_TC2_DOWN', 'TC2' }, { 'MGUK_DELIVERY_UP', 'MGUK_DELIVERY_DN', 'MGU-K' },
  { 'DRS', 'DRS', 'DRS' }, { 'KERS', 'KERS', 'KERS' }, { '__EXT_FFB_RESET', '__EXT_FFB_RESET', 'FFB reset' },
}

local layout = { graphOnly = false, clusterLayout = 1, map = 1, sound = 1, prevSound = 2, rev = 1, mapDev = '', mapBtn = -1, sndDev = '', sndBtn = -1, revDev = '', revBtn = -1 }
for _, s in ipairs(SLOTS) do
  layout[s.id .. 'fn'], layout[s.id .. 'dev'], layout[s.id .. 'up'], layout[s.id .. 'dn'] = 0, '', s.up or -1, s.dn or -1
end
local store = ac.storage(layout)
local cfg = nil              -- this car's entry in CARS, if it has one
local carId = ''
local started, startWait = false, 0
local mapTouched = false     -- the map has been set from here (until then the car's own default applies)
local mapTimer = 0
local banks = {}             -- [car] = true once its sound file is loaded
local status = { sound = '', bank = 1, err = '', note = '' }
local squeezed = false       -- the engine sound's revs are being scaled down to fit the borrowed sound
local soundScale = nil       -- when set: sound revs = engine revs x this, written every frame
local soundRpm = -1          -- what the sound reports its revs as, read back for the log
local binding, bindTimer, bindSkip = nil, 0, {}
local held = { map = false, sound = false, rev = false }
local learn = nil             -- { slot, step, timer, down }: a knob being taught on the Wheel knobs panel
local turned = {}            -- [slot id] = seconds since it was last turned, for the flash on the panel
local gameBtn = {}           -- [button] = what the game itself has that button doing
local soundLimiter = 0       -- the rev limit the engine sound was last set up for

local function hex(h, a)
  return rgbm(tonumber(h:sub(1, 2), 16) / 255, tonumber(h:sub(3, 4), 16) / 255, tonumber(h:sub(5, 6), 16) / 255, a or 1)
end
-- The panel is mostly see-through so the track stays visible behind it; BACKDROP is its opacity (0 = clear, 1 = solid).
local BACKDROP = 0.32
local COL = {
  panel = hex('0E1318', BACKDROP), edge = hex('8FA3B5', 0.28), text = hex('F4F7FA'), dim = hex('C3CED8'), faint = hex('93A3B2'),
  track = hex('0E1318', 0.55), tick = hex('C3CED8', 0.55), boost = hex('5DCAA5'), hp = hex('4C9BE8'), tq = hex('F2A93B'),
  red = hex('E24B4A'), live = hex('5DCAA5'), ring = hex('0E1318', 0.85), shade = hex('000000', 0.55), ink = hex('0E1318'),
}

local function num(v) return (type(v) == 'number' and v == v) and v or 0 end

local function clampIndex(i, n)
  i = math.floor(num(i))
  if i < 1 or i > n then return 1 end
  return i
end

local function resetPeaks()
  peak = { boost = 0, hp = 0, tq = 0, hpRpm = 0, tqRpm = 0 }
  bins = {}
end

local function resetG() G.up, G.down, G.left, G.right = 0, 0, 0, 0 end

-- the switches ---------------------------------------------------------------
local function fail(what, err)
  status.err = what .. ': ' .. tostring(err)
  ac.log('Boost and Power: ' .. status.err)
end

local function applyMap()
  if cfg == nil then return end
  local m = cfg.maps[clampIndex(store.map, #cfg.maps)]
  local ok, err = pcall(function() ac.setTurboWastegate(m.level, -1) end)
  if not ok then fail('map', err) end
  mapTouched = true
  mapTimer = 0
end

local function setMap(i)
  if cfg == nil or i == clampIndex(store.map, #cfg.maps) then return end
  store.map = clampIndex(i, #cfg.maps)
  applyMap()
  resetPeaks()                                          -- the other map has its own curve
  ac.log('Boost and Power: map ' .. cfg.maps[store.map].name)
end

local function flipMap()
  if cfg ~= nil then setMap(clampIndex(store.map, #cfg.maps) % #cfg.maps + 1) end
end

local function applyRev()
  if cfg == nil or cfg.revs == nil then return end
  local ok, err = pcall(function() ac.setExtraSwitch(cfg.revSwitch, clampIndex(store.rev, #cfg.revs) == 2) end)
  if not ok then fail('rev limit', err) end
end

local function setRev(i)
  if cfg == nil or cfg.revs == nil or i == clampIndex(store.rev, #cfg.revs) then return end
  store.rev = clampIndex(i, #cfg.revs)
  applyRev()
  resetPeaks()                                          -- the same power now sits at different revs
  ac.log('Boost and Power: rev limit ' .. cfg.revs[store.rev].name)
end

local function flipRev()
  if cfg ~= nil and cfg.revs ~= nil then setRev(clampIndex(store.rev, #cfg.revs) % #cfg.revs + 1) end
end

local function applySound()
  if cfg == nil then return end
  local s = cfg.sounds[clampIndex(store.sound, #cfg.sounds)]
  local E = ac.CarAudioEventID
  status.bank = 1
  for _, donor in ipairs({ s.car or false, s.turboFrom or false }) do
    if donor and not banks[donor] then
      local ok, found = pcall(function()
        return ac.loadSoundbank(ac.getFolder(ac.FolderID.ContentCars) .. '/' .. donor .. '/sfx/' .. donor .. '.bank',
          ROOT .. '/content/sfx/GUIDs.txt')
      end)
      banks[donor] = ok and found ~= false
      if not ok then fail('sound file', found) elseif found == false then fail('sound file', 'not found for ' .. donor) end
    end
    if donor and not banks[donor] then status.bank = 0 end
  end
  local from = '/cars/' .. (s.car or carId) .. '/'
  local own = '/cars/' .. carId .. '/'
  local turbo = s.turboFrom and ('/cars/' .. s.turboFrom .. '/') or ((s.car ~= nil and s.turbo) and from or own)
  local swaps = {
    { E.EngineExt, from .. 'engine_ext', LOUD.engine }, { E.EngineInt, from .. 'engine_int', LOUD.engine },
    { E.BackfireExt, from .. 'backfire_ext', LOUD.backfire }, { E.BackfireInt, from .. 'backfire_int', LOUD.backfire },
    { E.Limiter, from .. 'limiter', 1 }, { E.GearExt, from .. 'gear_ext', 1 }, { E.GearInt, from .. 'gear_int', 1 },
    { E.Turbo, turbo .. 'turbo', s.turboLoud or LOUD.turbo },
  }
  for i = 1, #swaps do
    local ok, err = pcall(function()
      ac.CarAudioTweak.replaceAudioEvent(swaps[i][1], swaps[i][2])
      ac.CarAudioTweak.setVolume(swaps[i][1], swaps[i][3])
    end)
    if not ok then fail('sound', err) end
  end
  -- A sound recorded for fewer revs than this engine turns is fed scaled revs, so it tops out at the limiter
  -- instead of running off the end of its recording. Sounds that reach the limiter by themselves are left alone.
  -- The pitch is raised by the same factor (up to 40%), so the note still climbs with the real revs.
  -- The scaled revs are written to the sound every frame, in script.update (the patch's remap-table call is broken).
  -- Only on the high rev limit: at the car's normal limit every sound is left exactly as it was recorded.
  local top = num(s.top)
  local normal = cfg.revs and cfg.revs[1].rpm or now.limiter
  local squeeze = top > 0 and now.limiter > top + 50 and now.limiter > normal + 50
  soundLimiter = now.limiter
  if squeeze or squeezed then
    local pitch = squeeze and math.min(1.4, now.limiter / top) or 1
    local ok, err = pcall(function()
      ac.CarAudioTweak.setPitch(E.EngineExt, pitch)
      ac.CarAudioTweak.setPitch(E.EngineInt, pitch)
      if not squeeze then                               -- hand the revs back to the game
        ac.CarAudioTweak.setParameter(E.EngineExt, 'rpms', now.rpm, false)
        ac.CarAudioTweak.setParameter(E.EngineInt, 'rpms', now.rpm, false)
      end
    end)
    squeezed = squeeze
    soundScale = squeeze and top / now.limiter or nil
    status.note = ok and (squeeze and string.format('revs scaled to %d pitch %.2f', top, pitch) or '') or ('pitch failed: ' .. tostring(err))
    if not ok then ac.log('Boost and Power: ' .. status.note) end
  end
  status.sound = s.name
  ac.log('Boost and Power: sound ' .. s.name)
end

local function stepSound(dir)                           -- next or previous sound in the list, round and round
  if cfg == nil then return end
  local n = #cfg.sounds
  store.prevSound = clampIndex(store.sound, n)
  store.sound = (store.prevSound - 1 + dir) % n + 1
  applySound()
end

local function cycleSound() stepSound(1) end            -- on-screen click

local function flipSound()                              -- wheel button: back and forth between the last two
  if cfg == nil then return end
  local n = #cfg.sounds
  local a, b = clampIndex(store.sound, n), clampIndex(store.prevSound, n)
  if a == b then b = a % n + 1 end
  store.sound, store.prevSound = b, a
  applySound()
end

local function deviceIndex(name)
  if name == nil or name == '' then return nil end
  for d = 0, ac.getJoystickCount() - 1 do
    if ac.getJoystickName(d) == name then return d end
  end
  return nil
end

local function startBinding(which)                      -- next wheel button pressed becomes this switch
  binding, bindTimer, bindSkip = which, 0, {}
  for d = 0, ac.getJoystickCount() - 1 do
    for b = 0, ac.getJoystickButtonsCount(d) - 1 do
      if ac.isJoystickButtonPressed(d, b) then bindSkip[d * 1000 + b] = true end   -- already held: not the one being chosen
    end
  end
end

local function slotDevice(s)                            -- which controller a knob is on (the wheelbase unless taught otherwise)
  local name = store[s.id .. 'dev']
  if name == nil or name == '' then return 0 end
  return deviceIndex(name)
end

local function turn(s, dir)                             -- a knob moved one click: dir is 1 (right) or -1 (left)
  turned[s.id] = 0
  if cfg == nil then return end
  local fn, both = num(store[s.id .. 'fn']), num(store[s.id .. 'dn']) >= 0
  if fn == 1 then
    if both then setMap(dir > 0 and #cfg.maps or 1) else flipMap() end
  elseif fn == 2 and cfg.revs ~= nil then
    if both then setRev(dir > 0 and #cfg.revs or 1) else flipRev() end
  elseif fn == 3 then
    stepSound(both and dir or 1)
  end
end

local function pollKnobs(dt)
  for id, age in pairs(turned) do turned[id] = age + dt end
  if learn ~= nil then                                  -- teaching: the next button to go down is this knob's
    learn.timer = learn.timer + dt
    for d = 0, ac.getJoystickCount() - 1 do
      for b = 0, ac.getJoystickButtonsCount(d) - 1 do
        local key, down = d * 1000 + b, ac.isJoystickButtonPressed(d, b)
        if down and not learn.down[key] and learn.ready then
          local id = learn.slot.id
          if learn.step == 1 then
            store[id .. 'dev'], store[id .. 'up'], store[id .. 'dn'] = ac.getJoystickName(d) or '', b, -1
            learn.step, learn.timer, learn.first = 2, 0, key
          elseif key ~= learn.first then
            store[id .. 'dn'] = b
            learn = nil
            return
          end
        end
        learn.down[key] = down
      end
    end
    learn.ready = true                                  -- the first pass only notes what is already held
    if learn.timer > 8 then learn = nil end             -- one button is enough: it then steps one way only
    return
  end
  for _, s in ipairs(SLOTS) do
    local up, dn = num(store[s.id .. 'up']), num(store[s.id .. 'dn'])
    if up >= 0 then
      local d = slotDevice(s)
      local a = d ~= nil and ac.isJoystickButtonPressed(d, up)
      local b = d ~= nil and dn >= 0 and ac.isJoystickButtonPressed(d, dn)
      if a and not s.heldUp then turn(s, 1) end
      if b and not s.heldDn then turn(s, -1) end
      s.heldUp, s.heldDn = a, b
    end
  end
end

local function readGameControls()                       -- so the panel can say what the game already does with a button
  local ok, err = pcall(function()
    local ini = ac.INIConfig.controlsConfig()
    for _, g in ipairs(GAME) do
      for k = 1, 2 do
        local b = num(ini:get(g[k], 'BUTTON', -1))
        if b >= 0 and num(ini:get(g[k], 'JOY', -1)) >= 0 then gameBtn[b] = g[3] end
      end
    end
  end)
  if not ok then ac.log('Boost and Power: could not read the game controls: ' .. tostring(err)) end
end

local function pollWheel(dt)
  if binding ~= nil then
    bindTimer = bindTimer + dt
    for d = 0, ac.getJoystickCount() - 1 do
      for b = 0, ac.getJoystickButtonsCount(d) - 1 do
        local down = ac.isJoystickButtonPressed(d, b)
        if not down then
          bindSkip[d * 1000 + b] = nil
        elseif not bindSkip[d * 1000 + b] then
          local name = ac.getJoystickName(d) or ''
          if binding == 'map' then store.mapDev, store.mapBtn = name, b
          elseif binding == 'rev' then store.revDev, store.revBtn = name, b
          else store.sndDev, store.sndBtn = name, b end
          held[binding] = true                          -- wait for release before it counts as a flip
          binding = nil
          return
        end
      end
    end
    if bindTimer > 8 then binding = nil end
    return
  end
  local d = store.mapBtn >= 0 and deviceIndex(store.mapDev) or nil
  local down = d ~= nil and ac.isJoystickButtonPressed(d, store.mapBtn)
  if down and not held.map then flipMap() end
  held.map = down
  d = store.sndBtn >= 0 and deviceIndex(store.sndDev) or nil
  down = d ~= nil and ac.isJoystickButtonPressed(d, store.sndBtn)
  if down and not held.sound then flipSound() end
  held.sound = down
  d = store.revBtn >= 0 and deviceIndex(store.revDev) or nil
  down = d ~= nil and ac.isJoystickButtonPressed(d, store.revBtn)
  if down and not held.rev then flipRev() end
  held.rev = down
end

-- sampling -------------------------------------------------------------------
local function loadCurve()
  curveTried = true
  local ok, lut = pcall(function() return ac.DataLUT11.carData(0, 'power.lut') end)
  if ok and lut ~= nil then curve = lut end
end

local function sample()
  local car = ac.getCar(0)
  if car == nil then return end
  if not curveTried then loadCurve() end
  now.rpm, now.boost, now.gas = num(car.rpm), math.max(0, num(car.turboBoost)), num(car.gas)
  now.gear = num(car.gear)
  now.mph = num(car.speedKmh) * 0.621371
  pcall(function() now.gx, now.gz = num(car.acceleration.x), num(car.acceleration.z) end)   -- g sideways and fore-aft
  now.brake, now.steer = num(car.brake), num(car.steer)
  local lim = num(car.rpmLimiter)
  if lim > 1000 then now.limiter = lim end
  local base = 0
  if curve ~= nil then
    local ok, v = pcall(function() return curve:get(now.rpm) end)
    if ok then base = math.max(0, num(v)) end
  end
  local nm = base * (1 + now.boost) * now.gas
  now.tq = nm * NM_TO_LBFT
  now.hp = nm * now.rpm / 7121                       -- N·m x rpm -> horsepower
  if now.boost > peak.boost then peak.boost = now.boost end
  if now.gas > 0.97 and now.rpm > 1500 then           -- full throttle: this is the engine's real output here
    if now.hp > peak.hp then peak.hp, peak.hpRpm = now.hp, now.rpm end
    if now.tq > peak.tq then peak.tq, peak.tqRpm = now.tq, now.rpm end
    local k = math.floor(now.rpm / 100)
    if bins[k] == nil or now.tq > bins[k] then bins[k] = now.tq end
  end
end

local function start(dt)                                -- once the car and the sound system are up
  local car = ac.getCar(0)
  if car == nil then return end
  local ready = true
  pcall(function() ready = ac.isAudioReady() end)
  if not ready then return end
  startWait = startWait + dt
  if startWait < 1.5 then return end
  started = true
  readGameControls()
  local ok, id = pcall(function() return ac.getCarID(0) end)
  carId = ok and tostring(id) or ''
  cfg = CARS[carId]
  if cfg == nil then return end
  store.map = clampIndex(store.map, #cfg.maps)
  store.sound = clampIndex(store.sound, #cfg.sounds)
  applyMap()
  applyRev()
  applySound()
end

local function feedSound()                              -- every frame while a sound is being stretched
  local E = ac.CarAudioEventID
  ac.CarAudioTweak.setParameter(E.EngineExt, 'rpms', now.rpm * soundScale, true)
  ac.CarAudioTweak.setParameter(E.EngineInt, 'rpms', now.rpm * soundScale, true)
end

function script.update(dt)
  sample()
  if soundScale ~= nil then
    local ok, err = pcall(feedSound)
    if not ok then
      soundScale = nil
      status.note = 'rev scaling failed: ' .. tostring(err)
      ac.log('Boost and Power: ' .. status.note)
    end
  end
  if started then
    local ok, err = pcall(pollKnobs, dt)
    if not ok then fail('wheel knob', err) end
  end
  if not started then
    local ok, err = pcall(start, dt)
    if not ok then started = true; fail('start', err) end
  elseif cfg ~= nil then
    local ok, err = pcall(pollWheel, dt)
    if not ok then fail('wheel button', err) end
    mapTimer = mapTimer + dt
    if mapTouched and mapTimer > 2 then applyMap(); applyRev() end   -- a session restart puts the car back on its defaults
    if math.abs(now.limiter - soundLimiter) > 100 then applySound() end   -- the rev limit moved: refit the sound to it
  end
  acc = acc + dt
  if acc < 0.2 then return end                         -- 5 Hz: a line the Mac can read to check the numbers
  acc = 0
  seq = seq + 1
  local car = ac.getCar(0)
  if car == nil then return end
  local wg0, wg1 = -1, -1
  pcall(function() wg0, wg1 = num(car.turboWastegates[0]), num(car.turboWastegates[1]) end)
  local mapName, level = '-', -1
  if cfg ~= nil then
    local m = cfg.maps[clampIndex(store.map, #cfg.maps)]
    mapName, level = (m.name:gsub(',', '')), m.level
  end
  pcall(function() soundRpm = num(ac.CarAudioTweak.getParameter(ac.CarAudioEventID.EngineExt, 'rpms')) end)
  io.save(OUT, string.format('%d,%.0f,%.2f,%.3f,%.1f,%.1f,%.2f,%.2f,%d,%s,%.2f,%d,%.3f,%.3f,%s,%d,%s,%.0f,%s,%d,%.0f,%.2f,%.2f,%.2f,%.1f',
    seq, now.rpm, now.gas, now.boost, now.tq, now.hp,
    num(car.drivetrainTorque), num(car.drivetrainPower), num(car.turboCount),
    mapName, level, mapTouched and 1 or 0, wg0, wg1, (status.sound:gsub(',', '')), status.bank, (status.err:gsub('[,\r\n]', ' ')), now.limiter, (status.note:gsub('[,\r\n]', ' ')), num(store.rev), soundRpm, now.gx, now.gz, now.brake, now.steer), true)
end

-- drawing helpers ------------------------------------------------------------
local function label(text, size, pos, color, align)   -- align: 0 left, 0.5 centre, 1 right
  if align and align > 0 then
    local w = ui.measureDWriteText(text, size).x
    pos = vec2(pos.x - w * align, pos.y)
  end
  ui.dwriteDrawText(text, size, vec2(pos.x + 1, pos.y + 1), COL.shade)
  ui.dwriteDrawText(text, size, pos, color or COL.text)
end

local function plain(text, size, pos, color, align)   -- no shadow: for text on a filled switch
  if align and align > 0 then
    local w = ui.measureDWriteText(text, size).x
    pos = vec2(pos.x - w * align, pos.y)
  end
  ui.dwriteDrawText(text, size, pos, color)
end

local function onArc(c, r, a) return vec2(c.x + math.cos(a) * r, c.y + math.sin(a) * r) end

local function arc(c, r, a0, a1, color, thick)         -- a stroke with rounded ends
  ui.pathClear()
  ui.pathArcTo(c, r, a0, a1, 48)
  ui.pathStroke(color, false, thick)
  ui.drawCircleFilled(onArc(c, r, a0), thick / 2, color, 16)
  ui.drawCircleFilled(onArc(c, r, a1), thick / 2, color, 16)
end

local function bar(p, w, h, f, color)
  ui.drawRectFilled(p, vec2(p.x + w, p.y + h), COL.track, h / 2)
  if f > 0.01 then ui.drawRectFilled(p, vec2(p.x + w * math.min(1, f), p.y + h), color, h / 2) end
end

local function thousands(v)
  local s = string.format('%.0f', v)
  local out = s:reverse():gsub('(%d%d%d)', '%1,'):reverse()
  if out:sub(1, 1) == ',' then out = out:sub(2) end
  return out
end

-- the pieces -----------------------------------------------------------------
local function drawDial(o)
  local c, r = vec2(o.x + 85, o.y + 82), 62
  local a0, a1 = math.pi * 0.75, math.pi * 2.25
  local psi, peakPsi = now.boost * BAR_TO_PSI, peak.boost * BAR_TO_PSI
  local top = math.max(15, math.ceil((peakPsi + 1) / 10) * 10)       -- scale grows in tens of psi with the best boost seen
  arc(c, r, a0, a1, COL.track, 10)
  local f = math.min(1, psi / top)
  if f > 0.004 then arc(c, r, a0, a0 + (a1 - a0) * f, COL.boost, 10) end
  for i = 0, 8 do
    local a = a0 + (a1 - a0) * i / 8
    ui.drawLine(onArc(c, r - 9, a), onArc(c, r - 14, a), COL.tick, 1)
  end
  if peak.boost > 0 then
    local ap = a0 + (a1 - a0) * math.min(1, peakPsi / top)
    ui.drawLine(onArc(c, r - 8, ap), onArc(c, r + 8, ap), COL.text, 2)
  end
  label(string.format('%.1f', psi), 32, vec2(c.x, c.y - 30), COL.text, 0.5)
  label('psi boost', 11, vec2(c.x, c.y + 8), COL.dim, 0.5)
  label(string.format('%.2f bar', now.boost), 11, vec2(c.x, c.y + 24), COL.boost, 0.5)
  label('0', 11, vec2(o.x + 26, o.y + 134), COL.faint, 0.5)
  label(string.format('%d', top), 11, vec2(o.x + 144, o.y + 134), COL.faint, 0.5)
end

local function drawReadout(o, w, title, value, peakValue, peakRpm, color)
  label(title, 11, vec2(o.x, o.y + 16), COL.dim, 0)
  label(thousands(value), 30, vec2(o.x + w, o.y), COL.text, 1)
  bar(vec2(o.x, o.y + 40), w, 4, peakValue > 0 and value / peakValue or 0, color)
  label(string.format('peak %s at %s rpm', thousands(peakValue), thousands(math.floor(peakRpm / 50 + 0.5) * 50)),
    11, vec2(o.x + w, o.y + 48), COL.faint, 1)
end

local function niceTop(v)
  local steps = { 100, 200, 250, 500, 1000, 2000, 2500, 5000 }
  for i = 1, #steps do
    if v / steps[i] <= 4 then return math.ceil(v / steps[i]) * steps[i], steps[i] end
  end
  return math.ceil(v / 10000) * 10000, 10000
end

local function drawCurve(o, w, h)
  local padL, padB = 34, 18
  local x0, y0, pw, ph = o.x + padL, o.y + 4, w - padL, h - padB - 4
  local rpmMax = math.max(2000, now.limiter)
  local top, step = niceTop(math.max(200, peak.hp, peak.tq))
  local function X(rpm) return x0 + pw * math.min(1, rpm / rpmMax) end
  local function Y(v) return y0 + ph * (1 - math.min(1, v / top)) end
  local v = 0
  while v <= top + 0.5 do                                         -- one scale for both: horsepower and lb-ft
    ui.drawLine(vec2(x0, Y(v)), vec2(x0 + pw, Y(v)), COL.edge, 1)
    label(string.format('%.0f', v), 11, vec2(x0 - 6, Y(v) - 7), COL.faint, 1)
    v = v + step
  end
  local rstep = rpmMax > 12000 and 4000 or 2000
  local r = 0
  while r <= rpmMax do
    label(string.format('%dk', r / 1000), 11, vec2(X(r), y0 + ph + 3), COL.faint, 0.5)
    r = r + rstep
  end
  local keys = {}
  for k, _ in pairs(bins) do keys[#keys + 1] = k end
  table.sort(keys)
  if #keys >= 2 then
    ui.pathClear()
    for i = 1, #keys do
      local rpm = keys[i] * 100 + 50
      ui.pathLineTo(vec2(X(rpm), Y(bins[keys[i]] / NM_TO_LBFT * rpm / 7121)))
    end
    ui.pathStroke(COL.hp, false, 2)
    ui.pathClear()
    for i = 1, #keys do ui.pathLineTo(vec2(X(keys[i] * 100 + 50), Y(bins[keys[i]]))) end
    ui.pathStroke(COL.tq, false, 2)
  else
    label('Full-throttle pulls fill in the curve', 11, vec2(x0 + pw / 2, y0 + ph / 2 - 7), COL.faint, 0.5)
  end
  ui.drawLine(vec2(X(now.rpm), y0), vec2(X(now.rpm), y0 + ph), COL.tick, 1)   -- where the revs are now
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.hp)), 6, COL.ring, 16)           -- a dark ring keeps the dots readable on the lines
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.hp)), 4, COL.hp, 16)
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.tq)), 6, COL.ring, 16)
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.tq)), 4, COL.tq, 16)
end

local STRIP = 64                                        -- height of the rev strip at the bottom of the panel

-- Gear, a rev bar and the revs in large figures. The bar and the figures turn amber in the last 15% before the
-- limiter and red in the last 7%, so the limit can be seen coming without reading the number.
local function drawRevStrip(o, w)
  local gearText = now.gear < 0 and 'R' or (now.gear == 0 and 'N' or string.format('%d', now.gear))
  local frac = math.min(1, now.rpm / math.max(1, now.limiter))
  local hot = frac >= 0.93 and COL.red or (frac >= 0.85 and COL.tq or nil)
  ui.drawRect(o, vec2(o.x + 46, o.y + 46), hot or COL.edge, 8, nil, hot and 2 or 1)
  label(gearText, 30, vec2(o.x + 23, o.y + 3), COL.text, 0.5)
  local rpmText = thousands(math.floor(now.rpm / 10 + 0.5) * 10)
  local tw = ui.measureDWriteText('00,000', 30).x
  label(rpmText, 30, vec2(o.x + w, o.y - 2), hot or COL.text, 1)
  label(string.format('rpm   limit %s', thousands(now.limiter)), 11, vec2(o.x + w, o.y + 33), COL.faint, 1)
  local bx, bw = o.x + 58, w - 58 - tw - 14
  bar(vec2(bx, o.y + 8), bw, 14, frac, hot or COL.dim)
  ui.drawLine(vec2(bx + bw * 0.85, o.y + 3), vec2(bx + bw * 0.85, o.y + 27), COL.tq, 1)    -- amber from here
  ui.drawLine(vec2(bx + bw * 0.93, o.y + 3), vec2(bx + bw * 0.93, o.y + 27), COL.red, 2)   -- red from here
  local step = now.limiter > 10000 and 2000 or 1000                                          -- a tick every thousand or two
  local r = step
  while r < now.limiter do
    local x = bx + bw * r / now.limiter
    ui.drawLine(vec2(x, o.y + 26), vec2(x, o.y + 31), COL.tick, 1)
    label(string.format('%d', r / 1000), 9, vec2(x, o.y + 32), COL.faint, 0.5)
    r = r + step
  end
end

-- One switch: a caption, then a pill. Left click acts; right click waits for a wheel button to tie to it.
local function switchArea(id, p, w, hgt, which, onClick)
  ui.setCursor(p)
  if ui.invisibleButton(id, vec2(w, hgt)) then onClick() end
  if ui.itemClicked(ui.MouseButton.Right) then startBinding(which) end
  if ui.itemHovered() then
    ui.setTooltip('Click to switch. Right-click, then press a wheel button, to put this switch on that button.')
  end
end

local function drawSwitches(o, w)
  local hgt = 24
  local mapI = clampIndex(store.map, #cfg.maps)
  -- engine map: one segment per map, the live one filled
  label('MAP', 11, vec2(o.x, o.y), COL.dim, 0)
  label(store.mapBtn >= 0 and ('btn ' .. (store.mapBtn + 1)) or 'hp', 9, vec2(o.x, o.y + 13), COL.faint, 0)
  local mx, seg = o.x + 36, 60
  local mw = seg * #cfg.maps
  ui.drawRectFilled(vec2(mx, o.y), vec2(mx + mw, o.y + hgt), COL.track, hgt / 2)
  ui.drawRect(vec2(mx, o.y), vec2(mx + mw, o.y + hgt), COL.edge, hgt / 2, nil, 1)
  if binding == 'map' then
    label('press a button', 11, vec2(mx + mw / 2, o.y + 5), COL.text, 0.5)
  else
    for i = 1, #cfg.maps do
      local sx = mx + seg * (i - 1)
      if i == mapI then
        local hot = i > 1
        ui.drawRectFilled(vec2(sx + 2, o.y + 2), vec2(sx + seg - 2, o.y + hgt - 2), hot and COL.red or COL.boost, (hgt - 4) / 2)
        plain(cfg.maps[i].name, 12, vec2(sx + seg / 2, o.y + 4), hot and COL.text or COL.ink, 0.5)
      else
        label(cfg.maps[i].name, 12, vec2(sx + seg / 2, o.y + 4), COL.faint, 0.5)
      end
    end
  end
  switchArea('##rigmap', vec2(mx, o.y), mw, hgt, 'map', flipMap)

  -- rev limit: the lit segment is the limit the car reports, so it only moves if the car really changed
  if cfg.revs ~= nil then
    local rx = mx + mw + 18
    label('REV', 11, vec2(rx, o.y), COL.dim, 0)
    label(store.revBtn >= 0 and ('btn ' .. (store.revBtn + 1)) or 'limit', 9, vec2(rx, o.y + 13), COL.faint, 0)
    local bx, rseg = rx + 32, 48
    local rw = rseg * #cfg.revs
    ui.drawRectFilled(vec2(bx, o.y), vec2(bx + rw, o.y + hgt), COL.track, hgt / 2)
    ui.drawRect(vec2(bx, o.y), vec2(bx + rw, o.y + hgt), COL.edge, hgt / 2, nil, 1)
    if binding == 'rev' then
      label('press', 11, vec2(bx + rw / 2, o.y + 5), COL.text, 0.5)
    else
      local live = 1
      for i = 2, #cfg.revs do
        if math.abs(now.limiter - cfg.revs[i].rpm) < math.abs(now.limiter - cfg.revs[live].rpm) then live = i end
      end
      for i = 1, #cfg.revs do
        local sx = bx + rseg * (i - 1)
        if i == live then
          local hot = i > 1
          ui.drawRectFilled(vec2(sx + 2, o.y + 2), vec2(sx + rseg - 2, o.y + hgt - 2), hot and COL.red or COL.boost, (hgt - 4) / 2)
          plain(cfg.revs[i].name, 12, vec2(sx + rseg / 2, o.y + 4), hot and COL.text or COL.ink, 0.5)
        else
          label(cfg.revs[i].name, 12, vec2(sx + rseg / 2, o.y + 4), COL.faint, 0.5)
        end
      end
    end
    switchArea('##rigrev', vec2(bx, o.y), rw, hgt, 'rev', flipRev)
  end

  -- engine sound, on its own line: the pill shows what is playing, a click moves to the next
  local sy = o.y + 30
  label('SOUND', 11, vec2(o.x, sy), COL.dim, 0)
  label(store.sndBtn >= 0 and ('btn ' .. (store.sndBtn + 1)) or 'engine', 9, vec2(o.x, sy + 13), COL.faint, 0)
  local px = o.x + 48
  local pw = math.max(60, o.x + w - px)
  ui.drawRectFilled(vec2(px, sy), vec2(px + pw, sy + hgt), COL.track, hgt / 2)
  ui.drawRect(vec2(px, sy), vec2(px + pw, sy + hgt), COL.tq, hgt / 2, nil, 1)
  if binding == 'sound' then
    label('press a wheel button', 11, vec2(px + pw / 2, sy + 5), COL.text, 0.5)
  else
    label(cfg.sounds[clampIndex(store.sound, #cfg.sounds)].name, 12, vec2(px + pw / 2, sy + 4), COL.text, 0.5)
  end
  switchArea('##rigsound', vec2(px, sy), pw, hgt, 'sound', cycleSound)
end

-- the Wheel knobs panel -------------------------------------------------------
local function knobValue(fn)                            -- what a knob's job is set to right now
  if cfg == nil then return '-' end
  if fn == 1 then return cfg.maps[clampIndex(store.map, #cfg.maps)].name end
  if fn == 2 then return cfg.revs and cfg.revs[clampIndex(store.rev, #cfg.revs)].name or '-' end
  if fn == 3 then return cfg.sounds[clampIndex(store.sound, #cfg.sounds)].name end
  return ''
end

local function drawSlot(s, c)
  local id = s.id
  local fn, up, dn = num(store[id .. 'fn']), num(store[id .. 'up']), num(store[id .. 'dn'])
  local color = s.color and hex(s.color) or COL.dim
  local flash = turned[id] ~= nil and turned[id] < 0.35
  local teaching = learn ~= nil and learn.slot == s
  local game = gameBtn[up] or gameBtn[dn]
  local r = s.thumb and 20 or 30
  if s.thumb then                                       -- a thumb wheel: a small upright pill
    ui.drawRectFilled(vec2(c.x - 13, c.y - r), vec2(c.x + 13, c.y + r), flash and hex('C3CED8', 0.55) or COL.track, 8)
    ui.drawRect(vec2(c.x - 13, c.y - r), vec2(c.x + 13, c.y + r), teaching and COL.text or COL.edge, 8, nil, teaching and 2 or 1)
    for k = -2, 2 do ui.drawLine(vec2(c.x - 8, c.y + k * 6), vec2(c.x + 8, c.y + k * 6), COL.tick, 1) end
  else                                                  -- a knob: a ring in the colour it glows on the wheel
    ui.drawCircleFilled(c, r, flash and hex(s.color, 0.45) or COL.track, 40)
    ui.pathClear()
    ui.pathArcTo(c, r, 0, math.pi * 2, 48)
    ui.pathStroke(teaching and COL.text or color, true, teaching and 4 or 3)
    local value = teaching and (learn.step == 1 and 'turn right' or 'turn left') or (fn > 0 and knobValue(fn) or '')
    label(value, value:len() > 6 and 10 or 13, vec2(c.x, c.y - (value:len() > 6 and 7 or 9)), COL.text, 0.5)
  end
  local y = c.y + r + 5
  local job = fn > 0 and FNS[fn] or (game or (up >= 0 and 'free' or 'not taught'))
  if s.thumb and teaching then job = learn.step == 1 and 'turn it up' or 'turn it down' end
  label(s.thumb and s.name or job, 11, vec2(c.x, y), (fn > 0 or s.thumb) and COL.text or COL.dim, 0.5)
  if s.thumb then
    label(fn > 0 and (FNS[fn] .. ' ' .. knobValue(fn)) or job, 9, vec2(c.x, y + 13), fn > 0 and COL.boost or COL.faint, 0.5)
  else
    label(fn > 0 and game and ('also ' .. game) or (up >= 0 and (dn >= 0 and string.format('btn %d / %d', up + 1, dn + 1) or string.format('btn %d', up + 1)) or 'right-click to teach'),
      9, vec2(c.x, y + 13), (fn > 0 and game) and COL.tq or COL.faint, 0.5)
  end
  ui.setCursor(vec2(c.x - r - 4, c.y - r - 2))
  if ui.invisibleButton('##knob' .. id, vec2(r * 2 + 8, r * 2 + 34)) then
    store[id .. 'fn'] = (fn + 1) % (#FNS + 1)             -- nothing, MAP, REV, SOUND, and round again
  end
  if ui.itemClicked(ui.MouseButton.Right) then
    learn = { slot = s, step = 1, timer = 0, down = {}, ready = false }
  end
  if ui.itemHovered() then
    ui.setTooltip('Click to choose what this does here: nothing, MAP, REV or SOUND.\nRight-click, then turn it one click right and one click left, to teach the panel which control it is.')
  end
end

function windowWheel(dt)
  local o = ui.getCursor()
  local space = ui.availableSpace()
  local w, h = space.x, space.y
  ui.drawRectFilled(o, vec2(o.x + w, o.y + h), COL.panel, 10)
  ui.drawRect(o, vec2(o.x + w, o.y + h), COL.edge, 10, nil, 1)
  label('Wheel knobs', 12, vec2(o.x + 16, o.y + 12), COL.dim, 0)
  label(learn ~= nil and 'teaching: turn the control one click each way' or 'click: choose its job   right-click: teach it', 11,
    vec2(o.x + w - 16, o.y + 13), learn ~= nil and COL.text or COL.faint, 1)
  local top, bottom = o.y + 44, o.y + h - 44
  for _, s in ipairs(SLOTS) do
    drawSlot(s, vec2(o.x + w * s.x, top + (bottom - top) * s.y))
  end
  if cfg == nil then
    label('MAP, REV and SOUND only act on cars that have them', 11, vec2(o.x + w / 2, o.y + h - 20), COL.faint, 0.5)
  end
end

-- the Cluster window ----------------------------------------------------------
-- Five round clocks in a row, the way a sports car's instrument cluster is laid out: speed, horsepower with torque,
-- a large rev counter in the middle, the power curve, and a small one for g-force. Three unlabelled lights sit
-- in them: the sound under the speed, the map under the power, the rev limit under the rev counter. Only the
-- clocks are drawn, so the track shows between and around them.
local K = {
  face = hex('07090B', 0.74), ring = hex('F4F7FA', 0.9), white = hex('F4F7FA'), ink = hex('0B0E11'), dimw = hex('F4F7FA', 0.55),
  off = hex('F4F7FA', 0.13), red = hex('E0312E'), amber = hex('F2A93B'), blue = hex('4C9BE8'), teal = hex('5DCAA5'),
}
local A0, A1 = math.pi * 0.75, math.pi * 2.25              -- every scale sweeps 270 degrees, gap at the bottom
local clusterTime = 0

-- The cluster has three looks, taken in turn as the revs climb towards the limit:
--   1  calm       white rev counter face, black figures               below 85% of the limit
--   2  warning    black face ringed with red ticks, white figures     85% to 93%
--   3  shift      solid red face, black figures, red clocks all round  from 93%
local function stage()
  local frac = math.max(0, math.min(1, now.rpm / math.max(1, now.limiter)))
  return frac >= 0.93 and 3 or (frac >= 0.85 and 2 or 1), frac
end

local function stroke(c, r, a0, a1, color, thick)          -- an arc with square ends
  ui.pathClear()
  ui.pathArcTo(c, r, a0, a1, math.max(8, math.floor((a1 - a0) * 14)))
  ui.pathStroke(color, false, thick)
end

local function centred(text, size, c, dy, color)
  local w = ui.measureDWriteText(text, size).x
  ui.dwriteDrawText(text, size, vec2(c.x - w / 2, c.y + dy), color)
end

-- A side clock: one big number, its unit, a thin scale with a red marker at the value and a tick at the best seen.
local function clock(c, r, s, value, top, unit, peakValue)
  local st = stage()
  local ring = st == 1 and K.ring or K.red                 -- the bezels go red with the rev counter
  ui.drawCircleFilled(c, r, K.face, 48)
  stroke(c, r - 5 * s, A0, A1, ring, 1.5 * s)
  for i = 0, 10 do
    local a = A0 + (A1 - A0) * i / 10
    ui.drawLine(onArc(c, r - 4 * s, a), onArc(c, r - (i % 5 == 0 and 13 or 9) * s, a), ring, (i % 5 == 0 and 2 or 1) * s)
  end
  local f = math.max(0, math.min(1, value / top))
  if f > 0.005 then stroke(c, r - 14 * s, A0, A0 + (A1 - A0) * f, K.red, 6 * s) end   -- the value, as a red arc
  if peakValue ~= nil and peakValue > 0 then
    local ap = A0 + (A1 - A0) * math.min(1, peakValue / top)
    ui.drawLine(onArc(c, r - 9 * s, ap), onArc(c, r - 21 * s, ap), K.white, 2 * s)       -- the best seen
  end
  local size = r * 0.56
  centred(thousands(value), size, c, -size * 0.66, K.white)
  centred(unit, math.max(9, r * 0.14), c, size * 0.50, st == 1 and K.dimw or K.red)
  centred('0', math.max(9, r * 0.13), onArc(c, r - 30 * s, A0), -6 * s, K.dimw)
  centred(thousands(top), math.max(9, r * 0.13), onArc(c, r - 34 * s, A1), -6 * s, K.dimw)
end

-- Horsepower and torque in one clock: horsepower large with a red arc, torque under it with a white arc inside.
-- A tick on each arc marks the best seen.
local function powerClock(c, r, s, hpTop, tqTop)
  local st = stage()
  local ring = st == 1 and K.ring or K.red
  ui.drawCircleFilled(c, r, K.face, 48)
  stroke(c, r - 5 * s, A0, A1, ring, 1.5 * s)
  for i = 0, 10 do
    local a = A0 + (A1 - A0) * i / 10
    ui.drawLine(onArc(c, r - 4 * s, a), onArc(c, r - (i % 5 == 0 and 13 or 9) * s, a), ring, (i % 5 == 0 and 2 or 1) * s)
  end
  local function value(radius, thick, v, top, peakValue, color)
    local f = math.max(0, math.min(1, v / top))
    stroke(c, radius, A0, A1, K.off, thick)
    if f > 0.005 then stroke(c, radius, A0, A0 + (A1 - A0) * f, color, thick) end
    if peakValue > 0 then
      local ap = A0 + (A1 - A0) * math.min(1, peakValue / top)
      ui.drawLine(onArc(c, radius - thick, ap), onArc(c, radius + thick, ap), K.white, 2 * s)
    end
  end
  value(r - 15 * s, 6 * s, now.hp, hpTop, peak.hp, K.red)
  value(r - 25 * s, 4 * s, now.tq, tqTop, peak.tq, K.white)
  centred(thousands(now.hp), r * 0.35, c, -r * 0.52, K.white)
  centred('hp', math.max(9, r * 0.12), c, -r * 0.16, K.red)
  centred(thousands(now.tq), r * 0.26, c, r * 0.00, K.white)
  centred('lb-ft', math.max(9, r * 0.12), c, r * 0.27, K.dimw)
end

-- The power curve inside a clock: horsepower in red and torque in white against revs, filled in by full-throttle
-- pulls, with a marker at the revs the engine is turning now.
local function graphClock(c, r, s)
  local st = stage()
  local ring = st == 1 and K.ring or K.red
  ui.drawCircleFilled(c, r, K.face, 48)
  stroke(c, r - 5 * s, A0 - 0.5, A1 + 0.5, ring, 1.5 * s)
  local x0, x1, y0, y1 = c.x - r * 0.62, c.x + r * 0.62, c.y - r * 0.46, c.y + r * 0.40
  local rpmMax = math.max(2000, now.limiter)
  local top = niceTop(math.max(200, peak.hp, peak.tq))
  local function X(rpm) return x0 + (x1 - x0) * math.min(1, rpm / rpmMax) end
  local function Y(v) return y1 - (y1 - y0) * math.min(1, v / top) end
  ui.drawLine(vec2(x0, y1), vec2(x1, y1), K.dimw, 1)
  ui.drawLine(vec2(x0, (y0 + y1) / 2), vec2(x1, (y0 + y1) / 2), K.off, 1)
  ui.drawLine(vec2(x0, y0), vec2(x1, y0), K.off, 1)
  local small = math.max(9, r * 0.11)
  ui.dwriteDrawText(thousands(top), small, vec2(x0, y0 - small * 1.25), K.dimw)
  ui.dwriteDrawText('hp', small, vec2(x1 - small * 4.6, y0 - small * 1.25), K.red)
  ui.dwriteDrawText('lb-ft', small, vec2(x1 - small * 2.9, y0 - small * 1.25), K.white)
  centred('0', small, vec2(x0, y1), 3 * s, K.dimw)
  centred(string.format('%dk', math.floor(rpmMax / 1000 + 0.5)), small, vec2(x1, y1), 3 * s, K.dimw)
  local keys = {}
  for k, _ in pairs(bins) do keys[#keys + 1] = k end
  table.sort(keys)
  if #keys >= 2 then
    ui.pathClear()
    for i = 1, #keys do ui.pathLineTo(vec2(X(keys[i] * 100 + 50), Y(bins[keys[i]]))) end
    ui.pathStroke(K.white, false, 2 * s)
    ui.pathClear()
    for i = 1, #keys do
      local rpm = keys[i] * 100 + 50
      ui.pathLineTo(vec2(X(rpm), Y(bins[keys[i]] / NM_TO_LBFT * rpm / 7121)))
    end
    ui.pathStroke(K.red, false, 2.5 * s)
  else
    centred('full throttle fills this in', small, c, -small * 0.6, K.dimw)
  end
  ui.drawLine(vec2(X(now.rpm), y0), vec2(X(now.rpm), y1), K.dimw, 1)
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.tq)), 3.5 * s, K.white, 12)
  ui.drawCircleFilled(vec2(X(now.rpm), Y(now.hp)), 4 * s, K.red, 12)
  centred('power curve', small, c, r * 0.60, K.dimw)
end

local function pill(id, c, w, hgt, text, color, onClick)   -- a small outlined light; a click works the switch
  ui.drawRect(vec2(c.x - w / 2, c.y - hgt / 2), vec2(c.x + w / 2, c.y + hgt / 2), color, hgt / 2, nil, 1.5)
  local size = hgt * 0.62
  centred(text, size, c, -size * 0.66, color)
  if onClick ~= nil then
    ui.setCursor(vec2(c.x - w / 2, c.y - hgt / 2))
    if ui.invisibleButton(id, vec2(w, hgt)) then onClick() end
  end
end

local function revCounter(c, r, s)
  local st, frac = stage()
  local shift = frac >= 0.985
  local blink = shift and math.floor(clusterTime * 10) % 2 == 0     -- at the shift point the rev light flashes
  ui.drawCircleFilled(c, r, K.face, 64)
  -- the rev light: a ring of segments round the outside that fills with the revs
  local n = 45
  local light = st == 1 and K.white or K.red
  for i = 0, n - 1 do
    local a = A0 + (A1 - A0) * i / n
    local lit = shift or (i + 0.5) / n <= frac
    stroke(c, r - 6 * s, a + 0.012, a + (A1 - A0) / n - 0.012, lit and (blink and K.white or light) or K.off, 8 * s)
  end
  -- the face with the thousands on it, in the look for this stage
  local rf = r - 30 * s
  local figures = K.ink
  if st == 1 then
    stroke(c, rf, A0 - 0.06, A1 + 0.06, K.white, 26 * s)
    stroke(c, rf, A0 + (A1 - A0) * 0.93, A1 + 0.06, K.red, 26 * s)          -- a red block where the limit is
  elseif st == 2 then
    figures = K.white
    stroke(c, rf, A0 - 0.06, A1 + 0.06, hex('07090B', 0.92), 26 * s)
    for i = 0, 90 do                                                          -- a ring of fine red ticks
      local a = A0 + (A1 - A0) * i / 90
      ui.drawLine(onArc(c, rf + 8 * s, a), onArc(c, rf + 13 * s, a), K.red, 1.5 * s)
    end
  else
    stroke(c, rf, A0 - 0.06, A1 + 0.06, K.red, 26 * s)
    stroke(c, rf, A0 + (A1 - A0) * 0.93, A1 + 0.06, K.ink, 26 * s)
  end
  local thousandsTop = math.floor(now.limiter / 1000 + 0.5)
  local size = math.max(10, (thousandsTop > 9 and 15 or 19) * s)
  for k = 0, thousandsTop do
    local a = A0 + (A1 - A0) * (k * 1000 / now.limiter)
    centred(string.format('%d', k), size, onArc(c, rf - (st == 2 and 3 * s or 0), a), -size * 0.66,
      (st ~= 2 and k * 1000 >= now.limiter * 0.93) and K.white or figures)
  end
  local zone = st == 1 and K.white or K.red
  -- the black middle: boost on a ring, the gear, the revs and the boost in figures
  local rc = r - 45 * s
  ui.drawCircleFilled(c, rc, hex('07090B', 0.92), 48)
  local psi = now.boost * BAR_TO_PSI
  local boostTop = math.max(15, math.ceil((math.max(peak.boost, now.boost) * BAR_TO_PSI + 1) / 10) * 10)
  stroke(c, rc - 8 * s, A0, A1, K.off, 6 * s)
  if psi > 0.2 then stroke(c, rc - 8 * s, A0, A0 + (A1 - A0) * math.min(1, psi / boostTop), K.teal, 6 * s) end
  local gearText = now.gear < 0 and 'R' or (now.gear == 0 and 'N' or string.format('%d', now.gear))
  local gs = rc * 0.78
  centred(gearText, gs, c, -gs * 0.82, K.white)
  centred(thousands(math.floor(now.rpm / 10 + 0.5) * 10), 15 * s, c, gs * 0.20, zone)
  if store.clusterLayout == 2 then
    local speed, big, tiny = string.format('%.0f', now.mph), 19 * s, 8 * s      -- the figure, with its unit kept very small
    local wn, wu = ui.measureDWriteText(speed, big).x, ui.measureDWriteText('mph', tiny).x
    local x, y = c.x - (wn + 3 * s + wu) / 2, c.y + gs * 0.20 + 17 * s
    ui.dwriteDrawText(speed, big, vec2(x, y), K.white)
    ui.dwriteDrawText('mph', tiny, vec2(x + wn + 3 * s, y + (big - tiny) * 0.80), K.dimw)
  else
    centred(string.format('%.1f psi', psi), 12 * s, c, gs * 0.20 + 19 * s, K.teal)
  end
  ui.setCursor(vec2(c.x - gs * 0.4, c.y - gs * 0.75))
  if ui.invisibleButton('##cllayout', vec2(gs * 0.8, gs * 0.85)) then store.clusterLayout = store.clusterLayout == 2 and 1 or 2 end
  -- the needle, over the face only
  local an = A0 + (A1 - A0) * frac
  ui.drawLine(onArc(c, rc + 2 * s, an), onArc(c, r - 2 * s, an), st == 3 and K.white or K.red, 4 * s)
  if cfg ~= nil and cfg.revs ~= nil then                   -- the rev limit in force, where a road car shows its drive mode
    local live = 1
    for i = 2, #cfg.revs do
      if math.abs(now.limiter - cfg.revs[i].rpm) < math.abs(now.limiter - cfg.revs[live].rpm) then live = i end
    end
    pill('##clrev', vec2(c.x, c.y + r - 20 * s), 52 * s, 17 * s, cfg.revs[live].name, live > 1 and K.red or K.white, flipRev)
  end
end

-- The small clock on the right: g-force. The dot shows the force on the driver: it moves up under braking, down
-- under power, and to the outside of a corner. Rings at 1 and 2 g, the outer ring is 3 g; a short trail follows the
-- dot. The four figures are the most seen in each direction since they were last cleared (a click clears them).
local function gForce(c, r, s, dt)
  ui.drawCircleFilled(c, r, K.face, 48)
  ui.drawCircle(c, r - 5 * s, stage() == 1 and K.ring or K.red, 48, 1.5 * s)
  local reach = r - 27 * s
  local scale = reach / 3
  ui.drawCircle(c, scale, K.off, 32, 1 * s)
  ui.drawCircle(c, scale * 2, K.off, 40, 1 * s)
  ui.drawCircle(c, reach, K.off, 48, 1 * s)
  ui.drawLine(vec2(c.x - reach, c.y), vec2(c.x + reach, c.y), K.off, 1)
  ui.drawLine(vec2(c.x, c.y - reach), vec2(c.x, c.y + reach), K.off, 1)
  local k = math.min(1, (dt or 0.016) * 12)                  -- a little smoothing so the dot does not jitter
  G.x, G.z = G.x + (now.gx - G.x) * k, G.z + (now.gz - G.z) * k
  if now.mph > 10 and math.abs(G.x) < 6 and math.abs(G.z) < 6 then        -- moving, and not a crash
    G.left, G.right = math.max(G.left, -G.x), math.max(G.right, G.x)
    G.up, G.down = math.max(G.up, -G.z), math.max(G.down, G.z)
  end
  local function spot(gx, gz)
    local m = math.sqrt(gx * gx + gz * gz)
    local f = m > 3 and 3 / m or 1                           -- held at the outer ring beyond 3 g
    return vec2(c.x + gx * f * scale, c.y + gz * f * scale)   -- checked on a drive: a right-hand corner reads negative sideways, braking negative fore-aft
  end
  G.timer = G.timer + (dt or 0.016)
  if G.timer > 0.05 then
    G.timer = 0
    table.insert(G.trail, 1, { G.x, G.z })
    G.trail[25] = nil
  end
  for i = #G.trail, 1, -1 do
    ui.drawCircleFilled(spot(G.trail[i][1], G.trail[i][2]), 2 * s, hex('F4F7FA', 0.45 * (1 - i / 26)), 8)
  end
  ui.drawCircleFilled(spot(G.x, G.z), 5 * s, K.red, 16)
  local size = math.max(9, r * 0.15)
  centred(string.format('%.1f', G.up), size, vec2(c.x, c.y - r + 8 * s), 0, K.white)
  centred(string.format('%.1f', G.down), size, vec2(c.x, c.y + r - 9 * s - size * 1.15), 0, K.white)
  ui.dwriteDrawText(string.format('%.1f', G.left), size, vec2(c.x - r + 9 * s, c.y - size * 0.6), K.white)
  local w = ui.measureDWriteText(string.format('%.1f', G.right), size).x
  ui.dwriteDrawText(string.format('%.1f', G.right), size, vec2(c.x + r - 9 * s - w, c.y - size * 0.6), K.white)
  ui.setCursor(vec2(c.x - reach, c.y - reach))
  if ui.invisibleButton('##clg', vec2(reach * 2, reach * 2)) then resetG() end
  if ui.itemHovered() then ui.setTooltip('Most g seen braking (top), under power (bottom) and cornering each way. Click to clear.') end
end

-- The cluster's settings (the cog on its title bar): which arrangement of the clocks to show.
function windowClusterSettings(dt)
  ui.text('Arrangement')
  if ui.radioButton('Speed on the left, boost in the rev counter', store.clusterLayout ~= 2) then store.clusterLayout = 1 end
  if ui.radioButton('Speed in the rev counter, boost on the left', store.clusterLayout == 2) then store.clusterLayout = 2 end
  ui.text('Clicking the gear figure in the rev counter also switches between them.')
end

function windowCluster(dt)
  clusterTime = clusterTime + (dt or 0.016)
  local o = ui.getCursor()
  local space = ui.availableSpace()
  local s = math.min(space.x / 980, space.y / 300)
  local function at(x, y) return vec2(o.x + space.x / 2 + (x - 490) * s, o.y + space.y / 2 + (y - 150) * s) end
  if ac.getCar(0) == nil then return end
  local hpTop = niceTop(math.max(400, peak.hp))
  local tqTop = niceTop(math.max(400, peak.tq))
  local mphTop = now.mph > 200 and 300 or 200
  if store.clusterLayout == 2 then                         -- boost takes the left clock, speed moves into the rev counter
    local peakPsi = peak.boost * BAR_TO_PSI
    clock(at(76, 180), 76 * s, s, now.boost * BAR_TO_PSI, math.max(15, math.ceil((peakPsi + 1) / 10) * 10), 'psi', peakPsi)
  else
    clock(at(76, 180), 76 * s, s, now.mph, mphTop, 'mph', nil)
  end
  gForce(at(904, 180), 76 * s, s, dt)
  powerClock(at(250, 168), 96 * s, s, hpTop, tqTop)
  if cfg ~= nil then                                       -- three unlabelled lights; a click works each switch
    local m = clampIndex(store.map, #cfg.maps)
    local pm, ps = at(250, 168), at(76, 180)
    pill('##clmap', vec2(pm.x, pm.y + 96 * s * 0.58), 60 * s, 16 * s, cfg.maps[m].name, m > 1 and K.red or K.white, flipMap)
    pill('##clsound', vec2(ps.x, ps.y + 76 * s * 0.62), 84 * s, 16 * s, cfg.sounds[clampIndex(store.sound, #cfg.sounds)].name, K.white, cycleSound)
  end
  graphClock(at(730, 168), 96 * s, s)
  revCounter(at(490, 150), 136 * s, s)
end

function windowMain(dt)
  local o = ui.getCursor()
  local space = ui.availableSpace()
  local w, h = space.x, space.y
  ui.drawRectFilled(o, vec2(o.x + w, o.y + h), COL.panel, 10)
  ui.drawRect(o, vec2(o.x + w, o.y + h), COL.edge, 10, nil, 1)
  if ac.getCar(0) == nil then
    label('Waiting for the car', 12, vec2(o.x + 16, o.y + 14), COL.dim, 0)
    return
  end
  local pad = 16
  local ix, iw = o.x + pad, w - pad * 2
  if store.graphOnly then                                         -- just the power curve: the Cluster window shows the rest
    ui.drawLine(vec2(ix, o.y + 19), vec2(ix + 14, o.y + 19), COL.hp, 2)
    label(string.format('Horsepower %s', thousands(now.hp)), 11, vec2(ix + 20, o.y + 12), COL.dim, 0)
    ui.drawLine(vec2(ix + 138, o.y + 19), vec2(ix + 152, o.y + 19), COL.tq, 2)
    label(string.format('Torque %s', thousands(now.tq)), 11, vec2(ix + 158, o.y + 12), COL.dim, 0)
    drawCurve(vec2(ix, o.y + 34), iw, math.max(60, h - 34 - 10))
    ui.setCursor(vec2(ix + iw - 118, o.y + 8))
    if ui.button('Reset', vec2(52, 20)) then resetPeaks(); resetG() end
    ui.setCursor(vec2(ix + iw - 60, o.y + 8))
    if ui.button('Full', vec2(60, 20)) then store.graphOnly = false end
    return
  end
  label('Boost and power', 12, vec2(ix, o.y + 12), COL.dim, 0)
  ui.drawCircleFilled(vec2(ix + iw - 30, o.y + 20), 3, COL.live, 12)
  label('live', 11, vec2(ix + iw, o.y + 13), COL.dim, 1)

  local top = o.y + 34
  drawDial(vec2(ix - 2, top))
  local nx = ix + 184
  local nw = iw - 184
  drawReadout(vec2(nx, top + 6), nw, 'Horsepower', now.hp, peak.hp, peak.hpRpm, COL.hp)
  drawReadout(vec2(nx, top + 80), nw, 'Torque lb-ft', now.tq, peak.tq, peak.tqRpm, COL.tq)

  local ly = top + 158                                            -- key: a short stroke beside each name
  ui.drawLine(vec2(ix, ly + 7), vec2(ix + 14, ly + 7), COL.hp, 2)
  label('Horsepower', 11, vec2(ix + 20, ly), COL.dim, 0)
  ui.drawLine(vec2(ix + 104, ly + 7), vec2(ix + 118, ly + 7), COL.tq, 2)
  label('Torque', 11, vec2(ix + 124, ly), COL.dim, 0)
  if status.err ~= '' then
    label('switch problem: see the log', 11, vec2(ix + iw, ly), COL.red, 1)
  elseif curve == nil then
    label("no power curve for this car: boost only", 11, vec2(ix + iw, ly), COL.faint, 1)
  else
    label('fills in at full throttle', 11, vec2(ix + iw, ly), COL.faint, 1)
  end

  local row = cfg ~= nil and 64 or 0                              -- room for the two lines of switches on cars that have them
  local cy = ly + 18
  local ch = math.max(70, h - (cy - o.y) - STRIP - 8 - row)
  drawCurve(vec2(ix, cy), iw, ch)
  if cfg ~= nil then drawSwitches(vec2(ix, o.y + h - STRIP - row), iw) end
  drawRevStrip(vec2(ix, o.y + h - STRIP + 6), iw)

  ui.setCursor(vec2(ix + 150, o.y + 8))                           -- a small reset in the header
  if ui.button('Reset', vec2(52, 20)) then resetPeaks() end
  ui.setCursor(vec2(ix + 208, o.y + 8))                           -- shrink the panel to the curve alone
  if ui.button('Graph only', vec2(84, 20)) then store.graphOnly = true end
end
