import { UserSession } from "../types/dashboard";
import { apiClient } from "../services/apiClient";

export class AuthContextManager {
    private session: UserSession = {
        token: "",
        username: "anonymous",
        isAuthenticated: false
    };

    public async login(username: string): Promise<UserSession> {
        const token = await apiClient.login(username);
        this.session = { token, username, isAuthenticated: true };
        return this.session;
    }

    public getSession(): UserSession {
        return this.session;
    }

    public logout(): void {
        this.session = { token: "", username: "", isAuthenticated: false };
    }
}

export const authContext = new AuthContextManager();

