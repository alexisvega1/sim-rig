-- Run the in-game app with stand-in game functions whose drawing calls write an SVG, to look at a window off-game.
--   lua -e "W=980 H=300 RPM=8200" tools/render_app.lua assetto-corsa/apps/rig_power/rig_power.lua windowCluster out.svg
-- GRAPH=true shows the gauge in its graph-only mode. Text widths are estimated, so spacing is close to the game's but not exact.
local out = {}
local function css(c) return string.format('rgba(%d,%d,%d,%.2f)', math.floor(c[1] * 255 + 0.5), math.floor(c[2] * 255 + 0.5), math.floor(c[3] * 255 + 0.5), c[4] or 1) end
vec2 = function(x, y) return { x = x, y = y } end
rgbm = function(r, g, b, a) return { r, g, b, a } end
local car = { rpm = RPM or 10400, turboBoost = 4.71, gas = 1, gear = 5, rpmLimiter = LIMIT or 12000, drivetrainTorque = 0, drivetrainPower = 0, turboCount = 2, speedKmh = 372, acceleration = { x = 1.6, y = 0, z = -1.9 }, brake = 0.8, steer = 40, turboWastegates = { [0] = 0.8, [1] = 0.8 } }
local stored = { map = 2, sound = 6, rev = 2, graphOnly = GRAPH or false }
local path = {}
ac = {
  FolderID = { Root = 1, ContentCars = 13 }, getFolder = function() return 'C:/ac' end,
  storage = function(t) return setmetatable({}, { __index = function(_, k) local v = stored[k]; if v == nil then v = t[k] end; return v end, __newindex = function(_, k, v) stored[k] = v end }) end,
  getCar = function() return car end, getCarID = function() return 'porsche_911_gt2_rs_wss_2000' end, isAudioReady = function() return true end,
  log = function() end, setTurboWastegate = function() end, loadSoundbank = function() return true end, setExtraSwitch = function() end,
  CarAudioEventID = { EngineExt = 0, EngineInt = 1, GearExt = 2, GearInt = 3, BackfireExt = 10, BackfireInt = 11, Limiter = 15, Turbo = 16 },
  CarAudioTweak = setmetatable({ getParameter = function() return 0 end }, { __index = function() return function() end end }),
  DataLUT11 = { carData = function() return { get = function(_, rpm) return 300 end } end },
  INIConfig = { controlsConfig = function() return { get = function() return -1 end } end },
  getJoystickCount = function() return 0 end, getJoystickName = function() return '' end, getJoystickButtonsCount = function() return 0 end, isJoystickButtonPressed = function() return false end,
}
ui = setmetatable({
  MouseButton = { Right = 1 }, getCursor = function() return vec2(0, 0) end, availableSpace = function() return vec2(W, H) end,
  measureDWriteText = function(t, s) return vec2(#tostring(t) * s * 0.56, s * 1.2) end,
  dwriteDrawText = function(t, s, p, c) out[#out + 1] = string.format('<text x="%.1f" y="%.1f" font-size="%.1f" fill="%s" font-family="Helvetica, Arial" font-weight="600">%s</text>', p.x, p.y + s * 0.92, s, css(c), tostring(t):gsub('&', '&amp;')) end,
  drawCircleFilled = function(c, r, col) out[#out + 1] = string.format('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s"/>', c.x, c.y, r, css(col)) end,
  drawCircle = function(c, r, col, seg, th) out[#out + 1] = string.format('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" stroke-width="%.1f"/>', c.x, c.y, r, css(col), th or 1) end,
  drawLine = function(a, b, col, th) out[#out + 1] = string.format('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%.1f"/>', a.x, a.y, b.x, b.y, css(col), th or 1) end,
  drawRect = function(a, b, col, round, _, th) out[#out + 1] = string.format('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" fill="none" stroke="%s" stroke-width="%.1f"/>', a.x, a.y, b.x - a.x, b.y - a.y, round or 0, css(col), th or 1) end,
  drawRectFilled = function(a, b, col, round) out[#out + 1] = string.format('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="%.1f" fill="%s"/>', a.x, a.y, b.x - a.x, b.y - a.y, round or 0, css(col)) end,
  pathClear = function() path = {} end,
  pathArcTo = function(c, r, a0, a1, n) for i = 0, n do local a = a0 + (a1 - a0) * i / n; path[#path + 1] = string.format('%.1f,%.1f', c.x + math.cos(a) * r, c.y + math.sin(a) * r) end end,
  pathLineTo = function(p) path[#path + 1] = string.format('%.1f,%.1f', p.x, p.y) end,
  pathStroke = function(col, closed, th) out[#out + 1] = string.format('<%s points="%s" fill="none" stroke="%s" stroke-width="%.1f"/>', closed and 'polygon' or 'polyline', table.concat(path, ' '), css(col), th or 1); path = {} end,
  invisibleButton = function() return false end, itemClicked = function() return false end, itemHovered = function() return false end, button = function(text, size) local p = CUR or vec2(0, 0); out[#out + 1] = string.format('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" fill="rgba(60,70,80,0.9)"/><text x="%.1f" y="%.1f" font-size="11" fill="#fff" font-family="Helvetica" text-anchor="middle">%s</text>', p.x, p.y, size.x, size.y, p.x + size.x / 2, p.y + 14, text); BUTTONS = true; return false end, setCursor = function(p) CUR = p end,
}, { __index = function() return function() end end })
script = {}
io.save = function() end
dofile(arg[1])
for i = 1, 40 do script.update(0.1) end
for r = 2000, car.rpmLimiter, 100 do car.rpm = r; car.turboBoost = math.min(4.71, math.max(0, (r - 2600) / 3000 * 4.71)); script.update(0.02) end   -- a pull through the revs fills the curve
car.turboBoost = 4.71
car.rpm = RPM or 10400
for i = 1, 3 do script.update(0.1) end
out = {}
for i = 1, 30 do out = {}; car.acceleration.x = 1.6 - i * 0.02; car.acceleration.z = -1.9 + i * 0.03; script.update(0.03); _G[arg[2]](0.03) end
local f = io.open(arg[3], 'w')
f:write(string.format('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d"><rect width="%d" height="%d" fill="#6c7f86"/><rect y="%d" width="%d" height="%d" fill="#3c4347"/>', W, H, W, H, math.floor(H * 0.55), W, math.floor(H * 0.45)))
f:write(table.concat(out, '\n')); f:write('</svg>'); f:close()
