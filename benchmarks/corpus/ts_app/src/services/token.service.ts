export interface TokenPayload {
    sub: string;
    role: string;
    iat: number;
    exp: number;
}

export class TokenService {
    constructor(private readonly secretKey: string) {}

    public generateToken(userId: string, role: string): string {
        const payload: TokenPayload = {
            sub: userId,
            role: role,
            iat: Date.now(),
            exp: Date.now() + 3600000
        };
        return `jwt.${Buffer.from(JSON.stringify(payload)).toString("base64")}.${this.secretKey}`;
    }

    public validateToken(tokenString: string): TokenPayload | null {
        if (!tokenString.startsWith("jwt.")) {
            return null;
        }
        const parts = tokenString.split(".");
        if (parts.length !== 3 || parts[2] !== this.secretKey) {
            return null;
        }
        try {
            return JSON.parse(Buffer.from(parts[1], "base64").toString("utf8")) as TokenPayload;
        } catch {
            return null;
        }
    }
}

