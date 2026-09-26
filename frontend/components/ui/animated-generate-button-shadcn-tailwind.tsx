"use client";

import * as React from "react";
import clsx from "clsx";

// Started from a shadcn style "animated generate button" snippet. That design assumed a dark
// button (black bezel, white letters, a lime wash); on a white surface those layers turned it
// grey and murky, so this is a clean white pill with the same props: solid white face, near black
// label, an always on soft glow, and a crossfade between the idle and active labels.

export type AnimatedGenerateButtonProps = {
  className?: string;
  labelIdle?: string;
  labelActive?: string;
  generating?: boolean;
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void;
  type?: "button" | "submit" | "reset";
  disabled?: boolean;
  id?: string;
  ariaLabel?: string;
};

export default function AnimatedGenerateButton({
  className,
  labelIdle = "Send",
  labelActive = "Starting",
  generating = false,
  onClick,
  type = "button",
  disabled = false,
  id,
  ariaLabel,
}: AnimatedGenerateButtonProps) {
  return (
    <button
      id={id}
      type={type}
      aria-label={ariaLabel || (generating ? labelActive : labelIdle)}
      aria-busy={generating}
      disabled={disabled}
      onClick={onClick}
      data-generating={generating}
      className={clsx("send-btn", className)}
    >
      <svg className="send-btn-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456ZM16.894 20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z" />
      </svg>
      {/* Both labels share one grid cell, sized to the longer one, so the text stays centred. */}
      <span className="send-btn-labels">
        <span className="send-btn-label" data-visible={!generating}>
          {labelIdle}
        </span>
        <span className="send-btn-label" data-visible={generating} aria-hidden>
          {labelActive}
        </span>
      </span>
      <style jsx>{`
        .send-btn {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 0.5rem;
          height: 2.75rem;
          padding: 0 1.25rem;
          border-radius: 999px;
          border: 1px solid #ffffff;
          background: #ffffff;
          color: #0a0a0a;
          font-weight: 600;
          font-size: 0.95rem;
          cursor: pointer;
          user-select: none;
          /* Always on glow: a white halo and a faint accent bloom. */
          box-shadow:
            0 0 0 1px rgb(255 255 255 / 0.5),
            0 0 16px 2px rgb(255 255 255 / 0.28),
            0 0 36px 6px color-mix(in oklab, var(--accent) 22%, transparent),
            0 6px 16px -6px rgb(0 0 0 / 0.6);
          transition:
            box-shadow 200ms ease,
            transform 150ms cubic-bezier(0.23, 1, 0.32, 1);
        }

        .send-btn-icon {
          width: 1.1rem;
          height: 1.1rem;
          fill: #0a0a0a;
        }

        .send-btn-labels {
          display: grid;
          grid-template-areas: "stack";
        }
        .send-btn-label {
          grid-area: stack;
          text-align: center;
          transition: opacity 200ms ease;
        }
        .send-btn-label[data-visible="false"] {
          opacity: 0;
        }

        @media (hover: hover) and (pointer: fine) {
          .send-btn:not(:disabled):hover {
            box-shadow:
              0 0 0 1px rgb(255 255 255 / 0.7),
              0 0 22px 4px rgb(255 255 255 / 0.4),
              0 0 48px 10px color-mix(in oklab, var(--accent) 32%, transparent),
              0 8px 20px -6px rgb(0 0 0 / 0.6);
          }
        }

        .send-btn:not(:disabled):active {
          transform: scale(0.97);
        }

        /* Loading: the sparkle pulses while the run starts. */
        .send-btn[data-generating="true"] .send-btn-icon {
          animation: send-btn-pulse 1s ease-in-out infinite;
        }
        @keyframes send-btn-pulse {
          50% {
            opacity: 0.35;
          }
        }

        /* Disabled keeps the black label and the glow; only the cursor says it cannot be pressed yet. */
        .send-btn:disabled {
          cursor: not-allowed;
        }

        /* The glow breathes slowly so the button always reads as lit. */
        .send-btn {
          animation: send-btn-glow 2.6s ease-in-out infinite;
        }
        @keyframes send-btn-glow {
          50% {
            box-shadow:
              0 0 0 1px rgb(255 255 255 / 0.8),
              0 0 26px 6px rgb(255 255 255 / 0.45),
              0 0 56px 14px color-mix(in oklab, var(--accent) 38%, transparent),
              0 6px 16px -6px rgb(0 0 0 / 0.6);
          }
        }

        @media (prefers-reduced-motion: reduce) {
          .send-btn,
          .send-btn[data-generating="true"] .send-btn-icon {
            animation: none;
          }
        }
      `}</style>
    </button>
  );
}
