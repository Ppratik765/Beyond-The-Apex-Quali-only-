import fastf1
import pandas as pd
import numpy as np
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, 'cache')
if not os.path.exists(CACHE_DIR):
    os.makedirs(CACHE_DIR)
fastf1.Cache.enable_cache(CACHE_DIR)

def get_telemetry_multi(year, race, session_type, driver_list):
    session = fastf1.get_session(year, race, session_type)
    session.load()
    
    # 1. Weather
    weather = {"air_temp": 0, "track_temp": 0, "humidity": 0, "rain": False}
    try:
        if hasattr(session, 'weather_data') and not session.weather_data.empty:
            w = session.weather_data
            weather = {
                "air_temp": round(float(w['AirTemp'].mean()), 1),
                "track_temp": round(float(w['TrackTemp'].mean()), 1),
                "humidity": round(float(w['Humidity'].mean()), 1),
                "rain": bool(w['Rainfall'].any())
            }
    except:
        pass

    # 2. Reference Lap
    pole_lap = session.laps.pick_fastest()
    pole_tel = pole_lap.get_telemetry().add_distance()
    
    max_dist = 0
    loaded_laps = {}

    for d in driver_list:
        try:
            lap = session.laps.pick_driver(d).pick_fastest()
            if lap is not None:
                telemetry = lap.get_telemetry().add_distance()
                max_dist = max(max_dist, telemetry['Distance'].max())
                loaded_laps[d] = {"lap": lap, "tel": telemetry}
        except:
            continue

    if max_dist == 0:
        raise Exception("No data found for these drivers.")

    x_new = np.linspace(0, max_dist, num=4000)
    pole_time_interp = np.interp(x_new, pole_tel['Distance'], pole_tel['Time'].dt.total_seconds())

    results = {}
    for d, data in loaded_laps.items():
        tel = data["tel"]
        lap = data["lap"]
        
        time_interp = np.interp(x_new, tel['Distance'], tel['Time'].dt.total_seconds())
        delta_to_pole = time_interp - pole_time_interp
        
        rpm = np.interp(x_new, tel['Distance'], tel['RPM']) if 'RPM' in tel else np.zeros_like(x_new)
        gear = np.interp(x_new, tel['Distance'], tel['nGear']) if 'nGear' in tel else np.zeros_like(x_new)
        
        # G-Force
        speed_ms = np.interp(x_new, tel['Distance'], tel['Speed']) / 3.6
        dv = np.gradient(speed_ms)
        dt = np.gradient(time_interp)
        dt[dt == 0] = 1e-6 
        long_accel_g = dv / dt / 9.81 
        
        # Sanitize
        long_accel_g = np.nan_to_num(long_accel_g, nan=0.0)
        rpm = np.nan_to_num(rpm, nan=0.0)
        delta_to_pole = np.nan_to_num(delta_to_pole, nan=0.0)
        
        results[d] = {
            "telemetry": {
                'distance': x_new.tolist(),
                'speed': np.interp(x_new, tel['Distance'], tel['Speed']).tolist(),
                'throttle': np.interp(x_new, tel['Distance'], tel['Throttle']).tolist(),
                'brake': (np.interp(x_new, tel['Distance'], tel['Brake'])*100).tolist(),
                'rpm': rpm.tolist(),
                'gear': gear.tolist(),
                'long_g': long_accel_g.tolist(),
                'delta_to_pole': delta_to_pole.tolist(),
                'time': time_interp.tolist() 
            },
            "sectors": [
                lap['Sector1Time'].total_seconds(), 
                lap['Sector2Time'].total_seconds(), 
                lap['Sector3Time'].total_seconds()
            ],
            "lap_time": lap.LapTime.total_seconds(),
            "tyre_info": {
                "compound": lap['Compound'],
                "age": int(lap['TyreLife']) if not pd.isna(lap['TyreLife']) else 0
            }
        }

    best_s1 = session.laps['Sector1Time'].min().total_seconds()
    best_s2 = session.laps['Sector2Time'].min().total_seconds()
    best_s3 = session.laps['Sector3Time'].min().total_seconds()

    return {
        "drivers": results,
        "session_best_sectors": [best_s1, best_s2, best_s3],
        "pole_lap_time": pole_lap.LapTime.total_seconds(),
        "weather": weather
    }

def generate_ai_insights(multi_data, d1, d2):
    if d1 not in multi_data['drivers'] or d2 not in multi_data['drivers']:
        return ["Insufficient data for AI comparison."]

    t1 = multi_data['drivers'][d1]['telemetry']
    t2 = multi_data['drivers'][d2]['telemetry']
    
    dist = np.array(t1['distance'])
    speed1 = np.array(t1['speed'])
    speed2 = np.array(t2['speed'])
    throttle1 = np.array(t1['throttle'])
    throttle2 = np.array(t2['throttle'])
    brake1 = np.array(t1['brake'])
    brake2 = np.array(t2['brake'])
    time1 = np.array(t1['time']) 
    time2 = np.array(t2['time'])

    delta = time2 - time1 
    
    insights = []
    chunk_size = 250 # Smaller chunks for more detail
    
    for start in range(0, int(dist.max()), chunk_size):
        end = start + chunk_size
        mask = (dist >= start) & (dist < end)
        if not np.any(mask): continue
        
        delta_change = delta[mask][-1] - delta[mask][0]
        
        # Lower threshold for more sensitivity
        if abs(delta_change) > 0.04: 
            gainer = d1 if delta_change > 0 else d2
            loser = d2 if delta_change > 0 else d1
            gain_val = abs(delta_change)
            
            # Context Variables
            avg_speed = np.mean(speed1[mask])
            avg_brake = np.mean(brake1[mask])
            avg_throttle = np.mean(throttle1[mask])
            
            # SCENARIO 1: Heavy Braking Zone (Low Speed + High Brake)
            if avg_brake > 10 and avg_speed < 200:
                min_s1 = np.min(speed1[mask])
                min_s2 = np.min(speed2[mask])
                
                if (gainer == d1 and min_s1 > min_s2 + 5):
                     insights.append(f"Turn at {start}m: {gainer} carries +{int(min_s1 - min_s2)}km/h higher minimum speed.")
                elif (gainer == d1 and np.mean(brake1[mask]) < np.mean(brake2[mask])):
                     insights.append(f"Turn at {start}m: {gainer} brakes later/deeper, gaining {gain_val:.3f}s.")
                else:
                     insights.append(f"Braking at {start}m: {gainer} gains {gain_val:.3f}s on entry phase.")

            # SCENARIO 2: Traction Zone (Speed increasing + Throttle rising)
            elif np.mean(np.diff(speed1[mask])) > 0 and avg_throttle > 50:
                 if (gainer == d1 and np.mean(throttle1[mask]) > np.mean(throttle2[mask])):
                     insights.append(f"Exit at {start}m: {gainer} gets on power earlier (better traction), gaining {gain_val:.3f}s.")
                 else:
                     insights.append(f"Exit at {start}m: {gainer} has better drive out of the corner.")

            # SCENARIO 3: High Speed Straight
            elif avg_speed > 250:
                 speed_diff = np.mean(speed1[mask]) - np.mean(speed2[mask])
                 if abs(speed_diff) > 3:
                     insights.append(f"Straight at {start}m: {gainer} is faster by {int(abs(speed_diff))}km/h (Drag/Setup).")

    unique_insights = list(set(insights))
    # Sort insights by track position (start meter)
    # We can't easily sort list of strings, so we return first 6 found
    return unique_insights[:8] if unique_insights else ["No significant differences found."]