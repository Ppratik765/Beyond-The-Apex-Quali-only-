from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from analysis import get_telemetry_multi, generate_ai_insights

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/analyze")
def analyze_drivers(year: int, race: str, drivers: str):
    # 'drivers' comes in as "VER,LEC,HAM" string
    driver_list = [d.strip().upper() for d in drivers.split(',')]
    
    try:
        data = get_telemetry_multi(year, race, 'Q', driver_list)
        
        # Run AI on the first two drivers only (if available)
        insights = []
        if len(driver_list) >= 2:
            insights = generate_ai_insights(data, driver_list[0], driver_list[1])
        
        return {
            "status": "success",
            "data": data,
            "ai_insights": insights
        }
    except Exception as e:
        print(f"Error: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)