from flask import Flask, render_template_string, request, jsonify
import csv
import math

app = Flask(__name__)

# ==================== 后端：只负责返回最近黑点的距离 ====================

BLACKSPOTS = []

def load_blackspots(path):
    spots = []
    try:
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, start=2):
                try:
                    lat = float(row['lat'])
                    lng = float(row['lng'])
                    if not (22.1 <= lat <= 22.6 and 113.8 <= lng <= 114.5):
                        print(f"[警告] 第{i}行坐标不在香港范围内，已跳过")
                        continue
                    spots.append({
                        'id': row.get('id', str(i)),
                        'name': row.get('name', '未命名黑点'),
                        'lat': lat,
                        'lng': lng
                    })
                except (ValueError, KeyError) as e:
                    print(f"[警告] 第{i}行数据格式错误（{e}），已跳过")
                    continue
    except FileNotFoundError:
        print(f"[错误] 找不到文件：{path}")
        return []
    print(f"[初始化] 成功加载 {len(spots)} 个黑点")
    return spots

def haversine(lat1, lng1, lat2, lng2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

BLACKSPOTS = load_blackspots('blackspots.csv')

@app.route('/api/check_location', methods=['GET', 'POST'])
def check_location():
    try:
        if request.method == 'POST':
            data = request.get_json()
            lat = float(data['lat'])
            lng = float(data['lng'])
        else:
            lat = float(request.args.get('lat'))
            lng = float(request.args.get('lng'))
    except (TypeError, ValueError, KeyError):
        return jsonify({'error': '请提供有效的 lat 和 lng 参数'}), 400

    nearest = None
    nearest_dist = float('inf')
    for spot in BLACKSPOTS:
        dist = haversine(lat, lng, spot['lat'], spot['lng'])
        if dist < nearest_dist:
            nearest_dist = dist
            nearest = spot

    if nearest is None:
        return jsonify({'has_spot': False})

    return jsonify({
        'has_spot': True,
        'spot': {'id': nearest['id'], 'name': nearest['name']},
        'distance': round(nearest_dist, 1)
    })

# ==================== 前端页面 ====================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>智驾卫士 - 道路安全预警</title>
    <link rel="icon" href="data:,">
    <link rel="stylesheet" href="https://cdn.bootcdn.net/ajax/libs/leaflet/1.9.4/leaflet.css" />
    <script src="https://cdn.bootcdn.net/ajax/libs/leaflet/1.9.4/leaflet.js"></script>
    <style>
        body { margin: 0; padding: 20px; font-family: Arial, sans-serif; }
        #map { height: 500px; width: 100%; border-radius: 10px; border: 2px solid #333; }
        .btn { padding: 10px 20px; font-size: 16px; cursor: pointer; color: white; border: none; border-radius: 5px; margin-bottom: 10px; margin-right: 10px; display: inline-flex; align-items: center; justify-content: center; }
        .btn { background-color: #007BFF; }
        .btn:hover { background-color: #0056b3; }
        .btn-sim { background-color: #28a745; }
        .btn-sim:hover { background-color: #1e7e34; }
        .btn-reset { background-color: #6c757d; padding: 10px 15px; font-size: 18px; }
        .btn-reset:hover { background-color: #5a6268; }
        .status-ok { color: green; font-weight: bold; }
        .status-err { color: red; font-weight: bold; }
        .status-warn { color: orange; font-weight: bold; }
        #alertBox { display: none; padding: 15px; margin: 10px 0; border-radius: 5px; font-weight: bold; }
        #alertBox.approach { background-color: #fff3cd; border: 2px solid #ffc107; color: #856404; }
        #alertBox.inside { background-color: #ffcccc; border: 2px solid red; color: darkred; }
    </style>
</head>
<body>
    <h2>道路安全分心高危路段预警系统</h2>
    <button class="btn" id="trackBtn" onclick="toggleTracking()">开始监测定位</button>
    <button class="btn btn-sim" id="simBtn" onclick="toggleSimulation()">▶ 轨迹回放演示</button>
    <button class="btn btn-reset" onclick="resetAll()" title="重置系统">🔄</button>
    <p>系统状态：<span id="status" style="color: gray;">等待启动...</span></p>
    <div id="alertBox"></div>
    <div id="map"></div>

    <script>
        var map = L.map('map').setView([22.3193, 114.1694], 12);
        L.tileLayer('https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}', {
            subdomains: '1234', attribution: '&copy; 高德地图', maxZoom: 18, keepBuffer: 4
        }).addTo(map);

        var driverMarker = null;
        var driverCircle = null;
        var trackingInterval = null;
        var simInterval = null;
        var approachedSpots = new Set();
        var remindedSpots = new Set();
        var currentSpotId = null;
        var simIndex = 0;

        var currentSpeed = 0;
        var lastPos = null;
        var FENCE_RADIUS = 200;

        // ===== 预生成的粤语音频 =====
        var audioApproach = new Audio('/static/voice_approach.mp3');
        var audioInside = new Audio('/static/voice_inside.mp3');
        audioApproach.preload = 'auto';
        audioInside.preload = 'auto';

        // ===== 语音播放：优先 MP3，失败降级 speechSynthesis =====
        function playVoice(type) {
            var audio = (type === 'approach') ? audioApproach : audioInside;
            audio.currentTime = 0;
            var playPromise = audio.play();
            if (playPromise !== undefined) {
                playPromise.then(() => {
                    console.log('✅ 粤语音频播放：', type);
                }).catch(err => {
                    console.warn('❌ 音频播放失败（' + err.message + '），降级为浏览器语音');
                    fallbackSpeak(type);
                });
            }
        }

        function fallbackSpeak(type) {
            if (!('speechSynthesis' in window)) return;
            var text = (type === 'approach')
                ? '注意，前方交通黑点。'
                : '您已进入交通黑点，请保持专注，小心驾驶。';
            window.speechSynthesis.cancel();
            var u = new SpeechSynthesisUtterance(text);
            u.lang = 'zh-CN';
            u.rate = 0.95;
            window.speechSynthesis.speak(u);
        }

        // ===== 预录模拟轨迹 =====
        var simRoute = [
            {lat: 22.3110, lng: 114.1705}, {lat: 22.3100, lng: 114.1706},
            {lat: 22.3090, lng: 114.1707}, {lat: 22.3080, lng: 114.1708},
            {lat: 22.3070, lng: 114.1709}, {lat: 22.3060, lng: 114.1710},
            {lat: 22.3055, lng: 114.1711}, {lat: 22.3050, lng: 114.1712},
            {lat: 22.3048, lng: 114.1713}, {lat: 22.3046, lng: 114.1714},
            {lat: 22.3045, lng: 114.1715}, {lat: 22.3043, lng: 114.1716},
            {lat: 22.3040, lng: 114.1717}, {lat: 22.3035, lng: 114.1718},
            {lat: 22.3030, lng: 114.1719}, {lat: 22.3025, lng: 114.1720},
            {lat: 22.3020, lng: 114.1721}, {lat: 22.3015, lng: 114.1722},
            {lat: 22.3010, lng: 114.1723}, {lat: 22.3005, lng: 114.1724}
        ];

        function haversineJS(lat1, lng1, lat2, lng2) {
            var R = 6371000;
            var phi1 = lat1 * Math.PI / 180;
            var phi2 = lat2 * Math.PI / 180;
            var dphi = (lat2 - lat1) * Math.PI / 180;
            var dlambda = (lng2 - lng1) * Math.PI / 180;
            var a = Math.sin(dphi/2)**2 + Math.cos(phi1)*Math.cos(phi2)*Math.sin(dlambda/2)**2;
            return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
        }

        function estimateSpeed(lat, lng) {
            var now = Date.now();
            if (!lastPos) {
                lastPos = { lat: lat, lng: lng, time: now };
                return 0;
            }
            var dt = (now - lastPos.time) / 1000;
            var dist = haversineJS(lat, lng, lastPos.lat, lastPos.lng);
            lastPos = { lat: lat, lng: lng, time: now };
            if (dt <= 0) return currentSpeed;
            var instant = (dist / dt) * 3.6;
            currentSpeed = currentSpeed * 0.6 + instant * 0.4;
            return currentSpeed;
        }

        function getApproachDistance(speedKmh) {
            if (speedKmh < 30) return 400;
            if (speedKmh < 60) return 700;
            if (speedKmh < 80) return 900;
            return 1100;
        }

        function wgs84ToGcj02(lng, lat) {
            var a = 6378245.0, ee = 0.00669342162296594323, PI = Math.PI;
            function transformLat(x, y) {
                var ret = -100.0 + 2.0*x + 3.0*y + 0.2*y*y + 0.1*x*y + 0.2*Math.sqrt(Math.abs(x));
                ret += (20.0*Math.sin(6.0*x*PI) + 20.0*Math.sin(2.0*x*PI)) * 2.0/3.0;
                ret += (20.0*Math.sin(y*PI) + 40.0*Math.sin(y/3.0*PI)) * 2.0/3.0;
                ret += (160.0*Math.sin(y/12.0*PI) + 320*Math.sin(y*PI/30.0)) * 2.0/3.0;
                return ret;
            }
            function transformLng(x, y) {
                var ret = 300.0 + x + 2.0*y + 0.1*x*x + 0.1*x*y + 0.1*Math.sqrt(Math.abs(x));
                ret += (20.0*Math.sin(6.0*x*PI) + 20.0*Math.sin(2.0*x*PI)) * 2.0/3.0;
                ret += (20.0*Math.sin(x*PI) + 40.0*Math.sin(x/3.0*PI)) * 2.0/3.0;
                ret += (150.0*Math.sin(x/12.0*PI) + 300.0*Math.sin(x/30.0*PI)) * 2.0/3.0;
                return ret;
            }
            var dLat = transformLat(lng-105.0, lat-35.0);
            var dLng = transformLng(lng-105.0, lat-35.0);
            var radLat = lat/180.0*PI;
            var magic = Math.sin(radLat);
            magic = 1 - ee*magic*magic;
            var sqrtMagic = Math.sqrt(magic);
            dLat = (dLat*180.0)/((a*(1-ee))/(magic*sqrtMagic)*PI);
            dLng = (dLng*180.0)/(a/sqrtMagic*Math.cos(radLat)*PI);
            return [lng+dLng, lat+dLat];
        }

        // ===== 重置系统 =====
        function resetAll() {
            stopTracking();
            stopSimulation();
            
            // 清空状态
            approachedSpots.clear();
            remindedSpots.clear();
            currentSpotId = null;
            lastPos = null;
            currentSpeed = 0;
            simIndex = 0;
            
            // 清空地图标记
            if (driverMarker) map.removeLayer(driverMarker);
            if (driverCircle) map.removeLayer(driverCircle);
            driverMarker = null;
            driverCircle = null;
            
            // 重置视野到香港中心
            map.setView([22.3193, 114.1694], 12);
            
            // 重置 UI
            hideAlertBox();
            document.getElementById('trackBtn').innerText = "开始监测定位";
            document.getElementById('simBtn').innerText = "▶ 轨迹回放演示";
            setStatus("系统已重置，等待启动...", "");
        }

        // ===== 真实 GPS 监测模式 =====
        function toggleTracking() {
            var btn = document.getElementById('trackBtn');
            if (trackingInterval) {
                clearInterval(trackingInterval);
                trackingInterval = null;
                btn.innerText = "开始监测定位";
                setStatus("已停止监测", "");
                return;
            }
            stopSimulation();
            lastPos = null;
            currentSpeed = 0;
            btn.innerText = "停止监测";
            setStatus("监测中...", "status-warn");
            updateLocation();
            trackingInterval = setInterval(updateLocation, 3000);
        }

        function updateLocation() {
            if (!navigator.geolocation) return;
            navigator.geolocation.getCurrentPosition(onGpsSuccess, onGpsError, { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 });
        }

        function onGpsSuccess(position) {
            var lat = position.coords.latitude;
            var lng = position.coords.longitude;
            var accuracy = position.coords.accuracy;

            // 精度守门：如果误差大于 100 米，不触发围栏判断，只更新地图并提示
            if (accuracy > 100) {
                setStatus(`⚠ 当前定位精度过低 (${Math.round(accuracy)}米)，请移至开阔处`, "status-warn");
                var gcj = wgs84ToGcj02(lng, lat);
                drawDriver(gcj[1], gcj[0], accuracy);
                return; // 直接返回，不发给后端
            }

            var gcj = wgs84ToGcj02(lng, lat);
            drawDriver(gcj[1], gcj[0], accuracy);
            map.setView([gcj[1], gcj[0]], 15);
            sendToBackend(lat, lng, accuracy);
        }

        function onGpsError(error) {
            switch(error.code) {
                case error.PERMISSION_DENIED: setStatus("错误：您拒绝了定位权限", "status-err"); break;
                case error.POSITION_UNAVAILABLE: setStatus("错误：无法获取位置信息", "status-err"); break;
                case error.TIMEOUT: setStatus("错误：获取定位超时", "status-err"); break;
                default: setStatus("错误：未知定位错误", "status-err"); break;
            }
        }

        // ===== 轨迹回放模式 =====
        function toggleSimulation() {
            var btn = document.getElementById('simBtn');
            if (simInterval) {
                stopSimulation();
                btn.innerText = "▶ 轨迹回放演示";
                setStatus("回放已停止", "");
                return;
            }
            stopTracking();
            approachedSpots.clear();
            remindedSpots.clear();
            currentSpotId = null;
            lastPos = null;
            currentSpeed = 0;
            simIndex = 0;
            document.getElementById('alertBox').style.display = 'none';
            btn.innerText = "⏹ 停止回放";
            setStatus("轨迹回放中...", "status-warn");
            simInterval = setInterval(simStep, 2000);
            simStep();
        }

        function simStep() {
            if (simIndex >= simRoute.length) {
                stopSimulation();
                document.getElementById('simBtn').innerText = "▶ 轨迹回放演示";
                setStatus("回放结束", "status-ok");
                return;
            }
            var p = simRoute[simIndex];
            var gcj = wgs84ToGcj02(p.lng, p.lat);
            drawDriver(gcj[1], gcj[0], 10);
            map.setView([gcj[1], gcj[0]], 16);
            sendToBackend(p.lat, p.lng, 10);
            simIndex++;
        }

        function stopSimulation() {
            if (simInterval) { clearInterval(simInterval); simInterval = null; }
        }

        function stopTracking() {
            if (trackingInterval) {
                clearInterval(trackingInterval);
                trackingInterval = null;
                document.getElementById('trackBtn').innerText = "开始监测定位";
            }
        }

        function drawDriver(lat, lng, accuracy) {
            if (driverMarker) map.removeLayer(driverMarker);
            if (driverCircle) map.removeLayer(driverCircle);
            driverMarker = L.marker([lat, lng]).addTo(map);
            driverCircle = L.circle([lat, lng], {radius: accuracy, color: 'blue', fillOpacity: 0.1}).addTo(map);
        }

        function setAlertBox(text, className) {
            var box = document.getElementById('alertBox');
            box.style.display = 'block';
            box.className = className;
            box.innerText = text;
        }

        function hideAlertBox() {
            var box = document.getElementById('alertBox');
            box.style.display = 'none';
            box.className = '';
            box.innerText = '';
        }

        function sendToBackend(lat, lng, accuracy) {
            var speed = estimateSpeed(lat, lng);
            var approachDist = getApproachDistance(speed);
            console.log(`速度: ${speed.toFixed(1)} km/h，接近预警距离: ${approachDist} 米`);

            fetch('/api/check_location', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ lat: lat, lng: lng })
            })
            .then(res => res.json())
            .then(data => {
                if (!data.has_spot) {
                    hideAlertBox();
                    if (currentSpotId !== null) {
                        setStatus("已驶离高危路段", "status-ok");
                        currentSpotId = null;
                    } else {
                        setStatus(`监测中 - ${speed.toFixed(0)} km/h`, "status-ok");
                    }
                    return;
                }

                var spotId = data.spot.id;
                var spotName = data.spot.name;
                var dist = data.distance;

                if (dist <= FENCE_RADIUS) {
                    currentSpotId = spotId;
                    if (!remindedSpots.has(spotId)) {
                        remindedSpots.add(spotId);
                        setAlertBox(`⚠ 您已进入高危路段【${spotName}】，请保持专注！`, 'inside');
                        setStatus(`已触发提醒：${spotName}`, "status-err");
                        playVoice('inside');
                    } else {
                        setAlertBox(`⚠ 您正在高危路段【${spotName}】，请保持专注！`, 'inside');
                        setStatus(`正在通过：${spotName}`, "status-warn");
                    }

                } else if (dist <= approachDist) {
                    if (remindedSpots.has(spotId)) {
                        hideAlertBox();
                        setStatus(`即将驶离高危区域：${spotName}`, "status-ok");
                        return;
                    }
                    currentSpotId = spotId;
                    if (!approachedSpots.has(spotId)) {
                        approachedSpots.add(spotId);
                        setAlertBox(`⚠ 即将进入高危路段【${spotName}】，请提前提高警觉！`, 'approach');
                        setStatus(`接近预警：${spotName}`, "status-warn");
                        playVoice('approach');
                    } else {
                        setAlertBox(`⚠ 即将进入高危路段【${spotName}】，请提前提高警觉！`, 'approach');
                        setStatus(`接近预警：${spotName}`, "status-warn");
                    }

                } else {
                    hideAlertBox();
                    if (currentSpotId !== null) {
                        setStatus("已驶离高危路段", "status-ok");
                        currentSpotId = null;
                    } else {
                        setStatus(`监测中 - ${speed.toFixed(0)} km/h`, "status-ok");
                    }
                }
            })
            .catch(err => {
                console.error('后端通信失败:', err);
                setStatus("后端通信失败，请检查服务器", "status-err");
            });
        }

        function setStatus(text, className) {
            var el = document.getElementById('status');
            el.innerText = text;
            el.className = className;
        }
    </script>
</body>
</html>
"""

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)