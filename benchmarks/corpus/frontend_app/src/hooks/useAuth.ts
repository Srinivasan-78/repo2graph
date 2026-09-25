import { authContext } from "../context/AuthContext";
import { UserSession } from "../types/dashboard";

export function useAuth(): { session: UserSession; login: (u: string) => Promise<UserSession>; logout: () => void } {
    return {
        session: authContext.getSession(),
        login: (u: string) => authContext.login(u),
        logout: () => authContext.logout()
    };
}

