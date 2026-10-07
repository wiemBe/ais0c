import { QueryClientProvider } from "@tanstack/react-query";
import { useMemo } from "react";
import { createMemoryRouter, Navigate, RouterProvider, type RouteObject } from "react-router-dom";
import { createQueryClient } from "./api/queryClient";
import { SessionProvider, useMe, useSession } from "./api/session";
import { ErrorNotice, Loading } from "./components/common";
import { Layout } from "./components/Layout";
import { Admin } from "./pages/Admin";
import { CaseDetail } from "./pages/CaseDetail";
import { CaseQueue } from "./pages/CaseQueue";
import { Catalog } from "./pages/Catalog";
import { GroupDetail, GroupList } from "./pages/Groups";
import { Login } from "./pages/Login";
import { QaQueue } from "./pages/QaQueue";
import { Sla } from "./pages/Sla";

export const routes: RouteObject[] = [
  {
    path: "/",
    element: <Layout />,
    children: [
      { index: true, element: <CaseQueue /> },
      { path: "cases/:caseId", element: <CaseDetail /> },
      { path: "qa", element: <QaQueue /> },
      { path: "groups", element: <GroupList /> },
      { path: "groups/:groupId", element: <GroupDetail /> },
      { path: "catalog", element: <Catalog /> },
      { path: "admin", element: <Admin /> },
      { path: "sla", element: <Sla /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
];

function Gate({ initialPath }: { initialPath: string }) {
  const { token } = useSession();
  const me = useMe();
  const router = useMemo(
    () => createMemoryRouter(routes, { initialEntries: [initialPath] }),
    [initialPath],
  );
  if (token === null) return <Login />;
  if (me.isPending) return <Loading />;
  // 401 already ended the session; anything else (API down) is shown instead of the app.
  if (me.error) return <ErrorNotice error={me.error} />;
  return <RouterProvider router={router} />;
}

export function App({ initialPath = "/" }: { initialPath?: string }) {
  const client = useMemo(() => createQueryClient(), []);
  return (
    <QueryClientProvider client={client}>
      <SessionProvider>
        <Gate initialPath={initialPath} />
      </SessionProvider>
    </QueryClientProvider>
  );
}
