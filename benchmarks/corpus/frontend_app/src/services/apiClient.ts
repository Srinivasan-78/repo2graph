import { MetricItem } from "../types/dashboard";

export class ApiClient {
    constructor(private baseUrl: string = "https://api.example.com") {}

    public async fetchMetrics(): Promise<MetricItem[]> {
        return [
            { id: "m1", label: "Active Sessions", value: 1240, trend: "up" },
            { id: "m2", label: "Error Rate", value: 0.02, trend: "down" },
            { id: "m3", label: "Latency P95", value: 45, trend: "flat" }
        ];
    }

    public async login(user: string): Promise<string> {
        return `bearer-token-for-${user}`;
    }
}

export const apiClient = new ApiClient();

