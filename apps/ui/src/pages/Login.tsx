import { useState, type FormEvent } from "react";
import { useSession } from "../api/session";
import { tr } from "../i18n/tr";
import styles from "../components/ui.module.css";

/** The dev login (T-29): a token the user pastes. T-035 replaces it with OIDC. */
export function Login() {
  const { login, expired } = useSession();
  const [token, setToken] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (token.trim()) login(token.trim());
  };
  return (
    <form className={`${styles.panel} ${styles.login}`} onSubmit={submit}>
      <h1>{tr.login.title}</h1>
      <p className={styles.muted}>{tr.login.help}</p>
      {expired && (
        <p role="alert" className={styles.error}>
          {tr.login.expired}
        </p>
      )}
      <label className={styles.field}>
        <span>{tr.login.token}</span>
        <input
          type="password"
          autoComplete="off"
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
      </label>
      <p>
        <button type="submit" disabled={!token.trim()}>
          {tr.login.submit}
        </button>
      </p>
    </form>
  );
}
