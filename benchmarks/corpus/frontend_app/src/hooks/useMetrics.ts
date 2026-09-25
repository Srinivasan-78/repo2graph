import { apiClient } from "../services/apiClient";
import { MetricItem } from "../types/dashboard";

export class MetricsHook {
    public static async load(): Promise<MetricItem[]> {
        return apiClient.fetchMetrics();
    }
}

export function useMetrics(): Promise<MetricItem[]> {
    return MetricsHook.load();
}

