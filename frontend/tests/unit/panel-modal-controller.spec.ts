import { expect, test, vi } from "vitest";
import { PanelModel } from "../../src/panel-model";
import { serialiseEditable } from "../../src/effect-editor-model";
import { painted, panelModal } from "./panel-test-support";

test("unchanged explicit New drafts save directly with the header name", () => {
  const model = new PanelModel(() => undefined);
  model.isAdmin = true;
  model.name = "Jumping";
  model.content = painted();
  model.savedBaseline = serialiseEditable(model.name, model.content);
  model.editorSource = {
    kind: "new",
    owner: { section: "custom", category: "single-layer" },
  };
  const modal = panelModal(model);
  const save = vi.fn();

  modal.requestSave(save);

  expect(save).toHaveBeenCalledOnce();
  expect(model.saveNameDialogOpen).toBe(false);
});

test("Save As retains the dedicated naming dialog", async () => {
  const model = new PanelModel(() => undefined);
  model.isAdmin = true;
  const modal = panelModal(model);
  const saveAs = vi.fn().mockResolvedValue(true);

  modal.requestSaveAs({} as HTMLElement, "Jumping copy");
  expect(model.saveNameDialogOpen).toBe(true);
  expect(model.saveNameValue).toBe("Jumping copy");

  await modal.confirmNamedSave(saveAs);
  expect(saveAs).toHaveBeenCalledWith("Jumping copy");
  expect(model.saveNameDialogOpen).toBe(false);
});

test("Save As overwrite cancellation resumes naming and preserves return focus", async () => {
  const model = new PanelModel(() => undefined);
  model.isAdmin = true;
  const modal = panelModal(model);
  const focus = vi.fn();
  const returnFocus = {
    isConnected: true,
    focus,
  } as unknown as HTMLElement;

  modal.requestSaveAs(returnFocus, "Existing");
  const save = vi.fn(async () => {
    const overwrite = modal.requestOverwrite("Existing");
    modal.cancelOverwrite();
    return overwrite;
  });

  await modal.confirmNamedSave(save);

  expect(model.saveNameDialogOpen).toBe(true);
  expect(model.saveNameValue).toBe("Existing");
  modal.cancelSaveName();
  await Promise.resolve();
  expect(focus).toHaveBeenCalledOnce();
});

test("busy Save As ignores cancellation until persistence finishes", async () => {
  const model = new PanelModel(() => undefined);
  model.isAdmin = true;
  const modal = panelModal(model);
  let resolveSave!: (saved: boolean) => void;
  const saving = new Promise<boolean>((resolve) => {
    resolveSave = resolve;
  });

  modal.requestSaveAs({} as HTMLElement, "Existing");
  const operation = modal.confirmNamedSave(() => saving);
  await Promise.resolve();
  modal.showError("Unrelated preview failure.", {
    key: "unrelated-preview",
  });
  modal.closeError();
  modal.cancelSaveName();

  expect(model.modalState).toMatchObject({
    kind: "save-name",
    busy: true,
  });

  resolveSave(true);
  await operation;
  expect(model.modalState).toBeUndefined();
});

test("successful persistence keeps an apply error without resuming Save As", async () => {
  const model = new PanelModel(() => undefined);
  model.isAdmin = true;
  const modal = panelModal(model);

  modal.requestSaveAs({} as HTMLElement, "Existing");
  await modal.confirmNamedSave(async () => {
    model.reportError("Apply failed.", {
      title: "Live change failed",
      key: "apply-failed",
    });
    return true;
  });

  expect(model.modalState).toMatchObject({
    kind: "error",
    message: "Apply failed.",
  });
  expect(
    model.modalState?.kind === "error"
      ? model.modalState.resume
      : undefined,
  ).toBeUndefined();
  modal.closeError();
  expect(model.modalState).toBeUndefined();
});

test("error modals suspend and resume the active naming workflow", () => {
  const model = new PanelModel(() => undefined);
  model.isAdmin = true;
  const modal = panelModal(model);

  modal.requestSaveAs({} as HTMLElement, "Jumping copy");
  modal.showError("The library is unavailable.", {
    title: "Save failed",
    key: "save-failed",
  });

  expect(model.modalState).toMatchObject({
    kind: "error",
    title: "Save failed",
    resume: { kind: "save-name" },
  });

  modal.closeError();
  expect(model.saveNameDialogOpen).toBe(true);
  expect(model.saveNameValue).toBe("Jumping copy");
});

test("replacement errors preserve suspended workflows and retry after close", () => {
  const model = new PanelModel(() => undefined);
  const modal = panelModal(model);

  modal.requestTransition("Save", "Draft", false);
  modal.showError("First failure.", { key: "first" });
  modal.showError("Second failure.", { key: "second" });

  expect(model.modalState).toMatchObject({
    kind: "error",
    message: "Second failure.",
    resume: { kind: "pending-transition", saveName: "Draft" },
  });

  modal.closeError();
  expect(model.pendingTransitionDialog?.saveName).toBe("Draft");

  modal.showError("Second failure.", { key: "second" });
  expect(model.modalState).toMatchObject({
    kind: "error",
    message: "Second failure.",
  });
});

test("overwrite confirmation resolves without stacking modal workflows", async () => {
  const model = new PanelModel(() => undefined);
  const modal = panelModal(model);

  const cancelled = modal.requestOverwrite("Existing");
  modal.cancelOverwrite();
  await expect(cancelled).resolves.toBe(false);
  expect(model.modalState).toBeUndefined();

  const confirmed = modal.requestOverwrite("Existing");
  modal.confirmOverwrite();
  await expect(confirmed).resolves.toBe(true);
  expect(model.modalState).toBeUndefined();
});

test("nested overwrite cancellation preserves transition return focus", async () => {
  const model = new PanelModel(() => undefined);
  const modal = panelModal(model);
  const focus = vi.fn();
  const returnFocus = {
    isConnected: true,
    focus,
  } as unknown as HTMLElement;

  modal.requestTransition("Save", "Draft", false, returnFocus);
  const overwrite = modal.requestOverwrite("Existing");
  modal.cancelOverwrite();
  await expect(overwrite).resolves.toBe(false);
  modal.closeTransition(true);
  await Promise.resolve();

  expect(focus).toHaveBeenCalledOnce();
});
