import { UserService } from "../src/services/user.service";
import { TokenService } from "../src/services/token.service";

describe("UserService", () => {
    let tokenService: TokenService;
    let userService: UserService;

    beforeEach(() => {
        tokenService = new TokenService("test-secret");
        userService = new UserService(tokenService);
    });

    it("authenticates existing user with valid password", () => {
        const token = userService.login("user@example.com", "secure-password");
        expect(token).toBeDefined();
        expect(token.startsWith("jwt.")).toBe(true);
    });

    it("throws error when authenticating with invalid password", () => {
        expect(() => {
            userService.login("user@example.com", "wrong-password");
        }).toThrow("Invalid credentials");
    });

    it("finds user by id", () => {
        const user = userService.findUserById("user-123");
        expect(user).not.toBeNull();
        expect(user?.email).toBe("user@example.com");
    });
});

