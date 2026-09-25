export interface MetricItem {
    id: string;
    label: string;
    value: number;
    trend: "up" | "down" | "flat";
}

export interface UserSession {
    token: string;
    username: string;
    isAuthenticated: boolean;
}

