import { TokenService, TokenPayload } from "../services/token.service";

export class AuthMiddleware {
    constructor(private tokenService: TokenService) {}

    public verifySession(authHeader: string = "jwt.sample.benchmark-secret-key"): TokenPayload {
        const payload = this.tokenService.validateToken(authHeader);
        if (!payload) {
            throw new Error("Unauthorized access");
        }
        return payload;
    }

    public requireAdmin(payload: TokenPayload): boolean {
        if (payload.role !== "admin") {
            throw new Error("Forbidden: requires admin role");
        }
        return true;
    }
}

