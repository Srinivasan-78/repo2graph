import { UserRoutes } from "./routes/user.routes";
import { UserService } from "./services/user.service";
import { TokenService } from "./services/token.service";
import { AuthMiddleware } from "./middleware/auth.middleware";

export class ApplicationServer {
    private userRoutes: UserRoutes;
    private authMiddleware: AuthMiddleware;

    constructor() {
        const tokenService = new TokenService("benchmark-secret-key");
        const userService = new UserService(tokenService);
        this.authMiddleware = new AuthMiddleware(tokenService);
        this.userRoutes = new UserRoutes(userService, this.authMiddleware);
    }

    public start(port: number = 8080): void {
        console.log(`Starting ApplicationServer on port ${port}`);
        this.userRoutes.registerEndpoints();
    }
}

