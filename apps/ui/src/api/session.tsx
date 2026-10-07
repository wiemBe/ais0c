import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { get, readToken, storeToken } from "./client";
import { setUnauthorizedHandler } from "./queryClient";
import type { Me, Role } from "./types";

type SessionValue = {
  token: string | null;
  /** Set when the last session ended because the API answered 401. */
  expired: boolean;
  login: (token: string) => void;
  logout: (expired?: boolean) => void;
};

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [token, setToken] = useState<string | null>(() => readToken());
  const [expired, setExpired] = useState(false);

  const login = useCallback(
    (value: string) => {
      storeToken(value);
      client.clear();
      setExpired(false);
      setToken(value);
    },
    [client],
  );
  const logout = useCallback(
    (wasExpired = false) => {
      storeToken(null);
      client.clear();
      setExpired(wasExpired);
      setToken(null);
    },
    [client],
  );
  // A 401 from any request ends the session (T-029 criterion 2).
  useEffect(() => {
    setUnauthorizedHandler(() => logout(true));
    return () => setUnauthorizedHandler(null);
  }, [logout]);
  const value = useMemo(() => ({ token, expired, login, logout }), [token, expired, login, logout]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (value === null) throw new Error("useSession outside SessionProvider");
  return value;
}

/** The signed-in user and roles (`/me`). The roles are inclusive: admin covers hunter covers
 * operator, as the API applies them. */
export function useMe() {
  const { token } = useSession();
  return useQuery({
    queryKey: ["me", token],
    queryFn: () => get<Me>("/me"),
    enabled: token !== null,
    staleTime: Infinity,
  });
}

export function hasRole(me: Me | undefined, role: Role): boolean {
  return me?.roles.includes(role) ?? false;
}

export function useIsAdmin(): boolean {
  return hasRole(useMe().data, "admin");
}
