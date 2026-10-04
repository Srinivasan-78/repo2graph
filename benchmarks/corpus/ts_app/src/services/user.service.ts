import { UserModel } from "../models/user.model";
import { TokenService } from "./token.service";
import { hashPassword, verifyHash } from "../utils/crypto";

export class UserService {
    private users: Map<string, UserModel> = new Map();

    constructor(private tokenService: TokenService) {
        const defaultUser: UserModel = {
            id: "user-123",
            email: "user@example.com",
            passwordHash: hashPassword("secure-password"),
            role: "admin",
            active: true
        };
        this.users.set(defaultUser.id, defaultUser);
    }

    public findUserById(userId: string): UserModel | null {
        return this.users.get(userId) || null;
    }

    public login(email: string, passwordAttempt: string): string {
        for (const user of this.users.values()) {
            if (user.email === email && verifyHash(passwordAttempt, user.passwordHash)) {
                return this.tokenService.generateToken(user.id, user.role);
            }
        }
        throw new Error("Invalid credentials");
    }

    public deactivateUser(userId: string): boolean {
        const user = this.findUserById(userId);
        if (user) {
            user.active = false;
            return true;
        }
        return false;
    }
}

