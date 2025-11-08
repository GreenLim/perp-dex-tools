"""
FastAPI server for running trading bots via HTTP API
Supports both runbot.py and hedge_mode.py operations
"""

import asyncio
import subprocess
import os
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

from fastapi import FastAPI, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uvicorn


app = FastAPI(
    title="Perp DEX Trading Bot API",
    description="API for managing perpetual trading bots across multiple exchanges",
    version="1.0.0"
)


# ==================== Models ====================

class ExchangeType(str, Enum):
    BACKPACK = "backpack"
    EXTENDED = "extended"


class DirectionType(str, Enum):
    BUY = "buy"
    SELL = "sell"


class RunBotRequest(BaseModel):
    exchange: ExchangeType = Field(..., description="Exchange to trade on")
    ticker: str = Field(..., description="Trading pair ticker (e.g., ETH, BTC)")
    direction: DirectionType = Field(..., description="Trade direction: buy or sell")
    quantity: float = Field(..., gt=0, description="Quantity to trade")
    boost: bool = Field(False, description="Enable boost mode")


class HedgeModeRequest(BaseModel):
    exchange: ExchangeType = Field(..., description="Exchange to trade on (backpack or extended)")
    ticker: str = Field(..., description="Trading pair ticker (e.g., ETH, BTC)")
    size: float = Field(..., gt=0, description="Order size per iteration")
    iterations: int = Field(..., alias="iter", gt=0, description="Number of iterations to run")
    sleep: int = Field(0, ge=0, description="Sleep time in seconds after each step")
    fill_timeout: int = Field(5, ge=1, description="Timeout in seconds for maker order fills")


class BotStatus(BaseModel):
    status: str
    message: str
    timestamp: str


class ProcessInfo(BaseModel):
    pid: Optional[int] = None
    command: str
    start_time: str


# ==================== Global State ====================

running_processes: Dict[str, ProcessInfo] = {}


# ==================== Helper Functions ====================

def get_current_timestamp() -> str:
    """Get current timestamp in ISO format"""
    return datetime.now().isoformat()


async def run_command_async(command: list, task_id: str) -> Dict[str, Any]:
    """Run a command asynchronously and track its process"""
    try:
        # Start the process
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy()
        )

        # Store process info
        running_processes[task_id] = ProcessInfo(
            pid=process.pid,
            command=" ".join(command),
            start_time=get_current_timestamp()
        )

        # Wait for process to complete
        stdout, stderr = await process.communicate()

        # Remove from running processes
        if task_id in running_processes:
            del running_processes[task_id]

        return {
            "success": process.returncode == 0,
            "return_code": process.returncode,
            "stdout": stdout.decode() if stdout else "",
            "stderr": stderr.decode() if stderr else ""
        }

    except Exception as e:
        # Remove from running processes on error
        if task_id in running_processes:
            del running_processes[task_id]

        raise HTTPException(
            status_code=500,
            detail=f"Failed to execute command: {str(e)}"
        )


# ==================== API Endpoints ====================

@app.get("/", response_model=BotStatus)
async def health_check():
    """Health check endpoint"""
    return BotStatus(
        status="ok",
        message="Perp DEX Trading Bot API is running",
        timestamp=get_current_timestamp()
    )


@app.post("/runbot", response_model=BotStatus)
async def run_bot(request: RunBotRequest, background_tasks: BackgroundTasks):
    """
    Run a single trade using runbot.py

    Example:
    ```
    POST /runbot
    {
        "exchange": "backpack",
        "ticker": "ETH",
        "direction": "buy",
        "quantity": 0.1,
        "boost": true
    }
    ```
    """
    # Build command
    command = [
        "python", "runbot.py",
        "--exchange", request.exchange.value,
        "--ticker", request.ticker,
        "--direction", request.direction.value,
        "--quantity", str(request.quantity)
    ]

    if request.boost:
        command.append("--boost")

    # Generate task ID
    task_id = f"runbot_{request.exchange.value}_{request.ticker}_{get_current_timestamp()}"

    # Run in background
    background_tasks.add_task(run_command_async, command, task_id)

    return BotStatus(
        status="started",
        message=f"RunBot task started: {' '.join(command)}",
        timestamp=get_current_timestamp()
    )


@app.post("/hedge", response_model=BotStatus)
async def run_hedge_mode(request: HedgeModeRequest, background_tasks: BackgroundTasks):
    """
    Run hedge mode trading using hedge_mode.py

    Example:
    ```
    POST /hedge
    {
        "exchange": "extended",
        "ticker": "ETH",
        "size": 0.1,
        "iter": 20,
        "sleep": 5,
        "fill_timeout": 5
    }
    ```
    """
    # Build command
    command = [
        "python", "hedge_mode.py",
        "--exchange", request.exchange.value,
        "--ticker", request.ticker,
        "--size", str(request.size),
        "--iter", str(request.iterations),
        "--sleep", str(request.sleep),
        "--fill-timeout", str(request.fill_timeout)
    ]

    # Generate task ID
    task_id = f"hedge_{request.exchange.value}_{request.ticker}_{get_current_timestamp()}"

    # Run in background
    background_tasks.add_task(run_command_async, command, task_id)

    return BotStatus(
        status="started",
        message=f"Hedge mode task started: {' '.join(command)}",
        timestamp=get_current_timestamp()
    )


@app.get("/processes", response_model=Dict[str, ProcessInfo])
async def list_processes():
    """List all currently running bot processes"""
    return running_processes


@app.get("/logs/{exchange}/{ticker}")
async def get_logs(
    exchange: ExchangeType,
    ticker: str,
    lines: int = 100
):
    """
    Get recent logs for a specific exchange and ticker

    Parameters:
    - exchange: Exchange name (backpack or extended)
    - ticker: Trading pair ticker
    - lines: Number of recent lines to return (default: 100)
    """
    # Construct log file path
    log_file = f"logs/{exchange.value}_{ticker}_hedge_mode_log.txt"

    if not os.path.exists(log_file):
        raise HTTPException(
            status_code=404,
            detail=f"Log file not found: {log_file}"
        )

    try:
        # Read last N lines
        with open(log_file, 'r') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return {
            "exchange": exchange.value,
            "ticker": ticker,
            "log_file": log_file,
            "lines_returned": len(recent_lines),
            "logs": "".join(recent_lines)
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read log file: {str(e)}"
        )


@app.get("/trades/{exchange}/{ticker}")
async def get_trades(exchange: ExchangeType, ticker: str):
    """
    Get trade history from CSV file

    Parameters:
    - exchange: Exchange name (backpack or extended)
    - ticker: Trading pair ticker
    """
    # Construct CSV file path
    csv_file = f"logs/{exchange.value}_{ticker}_hedge_mode_trades.csv"

    if not os.path.exists(csv_file):
        raise HTTPException(
            status_code=404,
            detail=f"Trade history file not found: {csv_file}"
        )

    try:
        with open(csv_file, 'r') as f:
            content = f.read()

        return {
            "exchange": exchange.value,
            "ticker": ticker,
            "csv_file": csv_file,
            "content": content
        }

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read trade history: {str(e)}"
        )


@app.get("/logs/{exchange}/{ticker}/stream")
async def stream_logs(
    exchange: ExchangeType,
    ticker: str,
    follow: bool = True
):
    """
    Stream logs in real-time (like tail -f)

    Parameters:
    - exchange: Exchange name (backpack or extended)
    - ticker: Trading pair ticker
    - follow: If true, continuously stream new lines (default: true)

    Usage:
    ```bash
    # Stream logs in real-time
    curl "http://localhost:8000/logs/extended/ETH/stream"

    # Get existing logs without following
    curl "http://localhost:8000/logs/extended/ETH/stream?follow=false"
    ```
    """
    log_file = f"logs/{exchange.value}_{ticker}_hedge_mode_log.txt"

    if not os.path.exists(log_file):
        raise HTTPException(
            status_code=404,
            detail=f"Log file not found: {log_file}"
        )

    async def log_generator():
        """Generator that yields log lines"""
        try:
            # First, yield existing content
            with open(log_file, 'r') as f:
                for line in f:
                    yield line

            # If follow mode, continue watching for new lines
            if follow:
                with open(log_file, 'r') as f:
                    # Seek to end
                    f.seek(0, 2)

                    while True:
                        line = f.readline()
                        if line:
                            yield line
                        else:
                            # No new line, wait a bit
                            await asyncio.sleep(0.1)

        except Exception as e:
            yield f"Error reading log file: {str(e)}\n"

    return StreamingResponse(
        log_generator(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


@app.websocket("/ws/logs/{exchange}/{ticker}")
async def websocket_logs(websocket: WebSocket, exchange: str, ticker: str):
    """
    WebSocket endpoint for real-time log streaming

    Usage with websocat:
    ```bash
    websocat ws://localhost:8000/ws/logs/extended/ETH
    ```

    Usage with JavaScript:
    ```javascript
    const ws = new WebSocket('ws://localhost:8000/ws/logs/extended/ETH');
    ws.onmessage = (event) => {
        console.log(event.data);
    };
    ```
    """
    await websocket.accept()

    log_file = f"logs/{exchange}_{ticker}_hedge_mode_log.txt"

    if not os.path.exists(log_file):
        await websocket.send_text(f"Error: Log file not found: {log_file}")
        await websocket.close()
        return

    try:
        # Send existing content first
        with open(log_file, 'r') as f:
            for line in f:
                await websocket.send_text(line.rstrip('\n'))

        # Then follow new lines
        with open(log_file, 'r') as f:
            # Seek to end
            f.seek(0, 2)

            while True:
                line = f.readline()
                if line:
                    await websocket.send_text(line.rstrip('\n'))
                else:
                    # No new line, wait a bit
                    await asyncio.sleep(0.1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.send_text(f"Error: {str(e)}")
        await websocket.close()


# ==================== Main ====================

if __name__ == "__main__":
    # Run uvicorn server
    uvicorn.run(
        "api_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
