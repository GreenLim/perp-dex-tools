import { apiClient } from "../api";

export interface Task {
  id: number;
  name: string;
  status: "running" | "paused" | "stopped";
  startTime: string;
  duration: string;
  type: string;
  exchange: string;
}

export interface LogEntry {
  timestamp: string;
  level: "INFO" | "WARN" | "ERROR" | "SUCCESS";
  message: string;
}

export class TasksAPI {
  // Get all running tasks
  async getTasks(): Promise<Task[]> {
    try {
      const response = await apiClient.get<{ tasks: Task[] }>("/api/tasks");
      return response.tasks;
    } catch (error) {
      console.error("Failed to fetch tasks:", error);
      // Return mock data for now
      return [
        {
          id: 1,
          name: "Trading Bot - BTC/USDT",
          status: "running",
          startTime: "2024-11-08 10:30:00",
          duration: "2h 15m",
          type: "hedge_mode",
          exchange: "Binance",
        },
        {
          id: 2,
          name: "Arbitrage Scanner",
          status: "paused",
          startTime: "2024-11-08 09:00:00",
          duration: "3h 45m",
          type: "scanner",
          exchange: "Multi-Exchange",
        },
      ];
    }
  }

  // Get logs for a specific task
  async getTaskLogs(taskId: number, limit: number = 100): Promise<LogEntry[]> {
    try {
      const response = await apiClient.get<{ logs: LogEntry[] }>(
        `/api/tasks/${taskId}/logs?limit=${limit}`
      );
      return response.logs;
    } catch (error) {
      console.error("Failed to fetch logs:", error);
      return [];
    }
  }

  // Start a task
  async startTask(taskId: number): Promise<boolean> {
    try {
      await apiClient.post(`/api/tasks/${taskId}/start`);
      return true;
    } catch (error) {
      console.error("Failed to start task:", error);
      return false;
    }
  }

  // Pause a task
  async pauseTask(taskId: number): Promise<boolean> {
    try {
      await apiClient.post(`/api/tasks/${taskId}/pause`);
      return true;
    } catch (error) {
      console.error("Failed to pause task:", error);
      return false;
    }
  }

  // Stop a task
  async stopTask(taskId: number): Promise<boolean> {
    try {
      await apiClient.post(`/api/tasks/${taskId}/stop`);
      return true;
    } catch (error) {
      console.error("Failed to stop task:", error);
      return false;
    }
  }

  // Restart a task
  async restartTask(taskId: number): Promise<boolean> {
    try {
      await apiClient.post(`/api/tasks/${taskId}/restart`);
      return true;
    } catch (error) {
      console.error("Failed to restart task:", error);
      return false;
    }
  }

  // Subscribe to real-time logs using WebSocket
  subscribeToLogs(taskId: number, onMessage: (log: LogEntry) => void): () => void {
    const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
    const ws = new WebSocket(`${wsUrl}/ws/tasks/${taskId}/logs`);

    ws.onmessage = (event) => {
      try {
        const log = JSON.parse(event.data) as LogEntry;
        onMessage(log);
      } catch (error) {
        console.error("Failed to parse log message:", error);
      }
    };

    ws.onerror = (error) => {
      console.error("WebSocket error:", error);
    };

    // Return cleanup function
    return () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.close();
      }
    };
  }
}

export const tasksAPI = new TasksAPI();