import { useAuth } from "../hooks/useAuth";

export function renderHeader(): string {
    const { session } = useAuth();
    return `<header><h1>Analytics Platform</h1><span>User: ${session.username}</span></header>`;
}

