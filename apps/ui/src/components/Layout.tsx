import { NavLink, Outlet } from "react-router-dom";
import { useMe, useSession } from "../api/session";
import { tr } from "../i18n/tr";
import styles from "./ui.module.css";

const LINKS = [
  ["/", tr.nav.queue],
  ["/qa", tr.nav.qa],
  ["/groups", tr.nav.groups],
  ["/catalog", tr.nav.catalog],
  ["/admin", tr.nav.admin],
  ["/sla", tr.nav.sla],
] as const;

export function Layout() {
  const { logout } = useSession();
  const me = useMe().data;
  return (
    <div className={styles.layout}>
      <nav className={styles.nav} aria-label={tr.app.title}>
        <strong>{tr.app.title}</strong>
        {LINKS.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>
            {label}
          </NavLink>
        ))}
        {me && (
          <p className={styles.muted}>
            {me.display_name}
            <br />
            {me.roles.map((role) => tr.role[role]).join(", ")}
          </p>
        )}
        <button type="button" className="secondary" onClick={() => logout()}>
          {tr.nav.logout}
        </button>
      </nav>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
