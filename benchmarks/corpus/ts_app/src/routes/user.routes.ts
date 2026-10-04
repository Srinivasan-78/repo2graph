import { UserController } from "../controllers/user.controller";
import { UserService } from "../services/user.service";
import { AuthMiddleware } from "../middleware/auth.middleware";

export class UserRoutes {
    private controller: UserController;

    constructor(userService: UserService, private authMiddleware: AuthMiddleware) {
        this.controller = new UserController(userService);
    }

    public registerEndpoints(): void {
        // Registers user profile and authentication routes
        this.getProfileRoute();
        this.postLoginRoute();
    }

    public getProfileRoute(): void {
        this.authMiddleware.verifySession();
        this.controller.getUserProfile("user-123");
    }

    public postLoginRoute(): void {
        this.controller.authenticateUser("user@example.com", "secure-password");
    }
}

