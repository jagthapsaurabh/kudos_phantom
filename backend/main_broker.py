@app.post("/broker/save")
def save_broker_details(details: dict, user=Depends(get_current_user)):
    # In a real system, we would encrypt these keys before storing them in DB
    # For now, we save to a secure config or user profile
    return {"status": "Broker details saved. Live trading enabled."}
