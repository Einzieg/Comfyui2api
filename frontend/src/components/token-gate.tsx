import type React from "react";
import { KeyRound, ShieldCheck } from "lucide-react";
import { useState } from "react";

interface TokenGateProps {
  onSubmit: (token: string) => void;
}

export function TokenGate({ onSubmit }: TokenGateProps): React.ReactElement {
  const [token, setToken] = useState("");

  return (
    <main className="token-page">
      <section className="token-card">
        <div className="brand-mark">C</div>
        <h1>需要访问令牌</h1>
        <p>输入 API_TOKEN 或 ADMIN_TOKEN 后进入本地控制台。</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit(token.trim());
          }}
        >
          <label>
            <KeyRound size={18} />
            <input
              type="password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder="API Token / Admin Token"
              autoFocus
            />
          </label>
          <button className="primary-button" type="submit" disabled={!token.trim()}>
            进入控制台
          </button>
        </form>
        <small>UI 模式默认仅监听 127.0.0.1；公网监听建议启用 ADMIN_TOKEN。</small>
      </section>
      <section className="token-preview" aria-hidden="true">
        <span>
          <ShieldCheck size={16} />
          实时任务预览
        </span>
        <strong>Queue Console</strong>
        <div className="preview-metrics">
          <div>
            <b>128</b>
            <span>总任务</span>
          </div>
          <div>
            <b>112</b>
            <span>成功</span>
          </div>
          <div>
            <b>1</b>
            <span>运行中</span>
          </div>
        </div>
        <ul>
          <li>
            <span>task_w3Pg75B4...</span>
            <b className="ok">成功 100%</b>
          </li>
          <li>
            <span>task_P7NboZ...</span>
            <b>运行中 63%</b>
          </li>
          <li>
            <span>task_Dk7oL0...</span>
            <b className="bad">失败</b>
          </li>
        </ul>
      </section>
    </main>
  );
}
