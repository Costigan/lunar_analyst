import React from "react";
import { Button, Dialog, DialogBody, DialogFooter, FormGroup, Intent, TextArea } from "@blueprintjs/core";
import type { AssistantBugReportProgramState } from "../../services/assistantService";

type Props = {
  isOpen: boolean;
  reportText: string;
  submitting: boolean;
  errorText: string | null;
  activeSessionId: string | null;
  activeScenarioId: string | null;
  activeTurnId: string | null;
  activeProviderId: string;
  activeModelId: string;
  programState: AssistantBugReportProgramState;
  onClose: () => void;
  onReportTextChange: (value: string) => void;
  onSubmit: () => void;
};

export default function AssistantBugReportDialog(props: Props): JSX.Element {
  const canSubmit = Boolean(props.activeSessionId && props.reportText.trim().length > 0 && !props.submitting);

  return (
    <Dialog
      isOpen={props.isOpen}
      onClose={props.onClose}
      title="Report Assistant Bug"
      canEscapeKeyClose={!props.submitting}
      canOutsideClickClose={!props.submitting}
      className="assistant-bug-report-dialog"
    >
      <DialogBody>
        <p className="assistant-bug-report-copy">
          Capture the failure while it is still fresh. The bundle will include the active assistant context, a bounded
          backend log excerpt, and the current workspace state.
        </p>
        <FormGroup label="Short report">
          <TextArea
            fill
            growVertically
            rows={6}
            autoFocus
            value={props.reportText}
            onChange={(event) => props.onReportTextChange(event.target.value)}
            placeholder="What did the assistant do incorrectly, or fail to do?"
          />
        </FormGroup>
        <div className="assistant-bug-report-summary">
          <div><strong>Session:</strong> {props.activeSessionId || "(none)"}</div>
          <div><strong>Turn:</strong> {props.activeTurnId || "(latest available)"}</div>
          <div><strong>Scenario:</strong> {props.activeScenarioId || "(none)"}</div>
          <div><strong>Provider:</strong> {props.activeProviderId || "(none)"}</div>
          <div><strong>Model:</strong> {props.activeModelId || "(none)"}</div>
        </div>
        <FormGroup label="Program state snapshot">
          <TextArea fill growVertically rows={6} readOnly value={JSON.stringify(props.programState, null, 2)} />
        </FormGroup>
        {props.errorText ? <div className="assistant-bug-report-error">{props.errorText}</div> : null}
      </DialogBody>
      <DialogFooter
        actions={
          <>
            <Button text="Cancel" onClick={props.onClose} disabled={props.submitting} />
            <Button intent={Intent.PRIMARY} text={props.submitting ? "Capturing..." : "Capture Bug Report"} onClick={props.onSubmit} disabled={!canSubmit} />
          </>
        }
      />
    </Dialog>
  );
}
