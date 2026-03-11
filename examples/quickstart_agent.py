"""
Quickstart Agent - Minimal ACP agent server.

This is the simplest way to create an ACP-enabled agent.

Setup:
    1. Create .env file with RPC_URL, ACP_ADDRESS, PRIVATE_KEY
    2. Run: uvicorn quickstart_agent:app --port 8000

Environment:
    RPC_URL              - Blockchain RPC endpoint
    ACP_ADDRESS          - ACP contract address  
    PRIVATE_KEY          - Agent wallet private key
"""

from dotenv import load_dotenv
from bnbagent.quickstart import create_acp_app

load_dotenv()

# Create app - that's it!
app = create_acp_app(title="Quickstart Agent")


# Optional: Add your custom task handler
@app.post("/task")
async def handle_task(request):
    """Your agent's task handler."""
    body = await request.json()
    task = body.get("task", "")
    
    # Your AI logic here
    result = f"Processed: {task}"
    
    return {"result": result}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
