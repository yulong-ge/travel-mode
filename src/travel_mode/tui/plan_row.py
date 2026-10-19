"""One compact row; state controls come exclusively from its view model."""

from rich.text import Text
from textual.containers import Horizontal, Vertical
from textual.widgets import Checkbox, LoadingIndicator, Static

from travel_mode.i18n import t
from travel_mode.tui.view_models import plan_view
from travel_mode.tui.widgets import Button


class PlanCheck(Checkbox):
    def render(self):
        return Text("[x]" if self.value else "[ ]", style="bold" if self.has_focus else "")


class ActionChoice(Button):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The eight-cell action column includes the dropdown mark. Textual's
        # default one-cell line padding would clip "Freeze ▾".
        self.styles.line_pad = 0


class PlanRow(Vertical, can_focus=True):
    def __init__(self, model):
        super().__init__(id="row-" + model["id"], classes="plan-row")
        self.model = model
        # A queued Resize can arrive after recomposition removes our children.
        # Own the label directly instead of querying the dismantled DOM tree.
        self._name_label = Static(classes="plan-name")

    def compose(self):
        row = self.model
        view = plan_view(row)
        controls = view["operations"]
        row["operations"] = controls
        with Horizontal(classes="plan-line"):
            if "select" in controls:
                yield PlanCheck("", value=row["selected"], id="select-" + row["id"], classes="plan-mark")
            elif row["state"] == "running":
                yield LoadingIndicator(classes="plan-mark")
            else:
                mark = view["mark"]
                yield Static(mark, classes="plan-mark " + row["state"])
            self._name_label.update(self.name_text(view["name"]))
            yield self._name_label
            if "cycle" in controls:
                yield ActionChoice(
                    t("redesign.apply_action" if row["action"] == "apply" else "redesign." + row["action"])
                    + " ▾",
                    id="cycle-" + row["id"],
                    classes="plan-action",
                )
            else:
                yield Static(
                    Text(view["action"], overflow="ellipsis", no_wrap=True),
                    classes="plan-action " + row["state"],
                )
            yield Static(
                Text(view["reason"], overflow="ellipsis", no_wrap=True), classes="plan-reason wide-reason"
            )
            for action in controls:
                if action in {"retry", "force"}:
                    yield Button(
                        t("failure.retry" if action == "retry" else "impl.force_quit_3"),
                        id=action + "-" + row["id"],
                        classes="row-recovery",
                    )
        yield Static(
            Text(view["reason"], overflow="ellipsis", no_wrap=True), classes="plan-reason narrow-reason"
        )

    def name_text(self, name):
        label = Text(name, no_wrap=True)
        label.truncate(20 if self.app.size.width <= 80 else 24, overflow="ellipsis")
        return label

    def on_mount(self):
        self.set_class(self.app.size.width <= 80, "narrow")

    def on_resize(self, event):
        self.set_class(self.app.size.width <= 80, "narrow")
        self._name_label.update(self.name_text(plan_view(self.model)["name"]))

    def on_checkbox_changed(self, event):
        event.stop()
        self.model["selected"] = event.value
        self.app.update_primary()
        self.app.run_worker(self.app.render_observations(), group="attention", exclusive=True)

    def on_focus(self):
        self.app.focused_plan_id = self.model["id"]
        self.app.update_details()

    def on_descendant_focus(self, event):
        self.app.focused_plan_id = self.model["id"]
        self.app.update_details()


class EvidenceLine(Horizontal, can_focus=True):
    def __init__(self, *children, name, evidence=(), **kwargs):
        super().__init__(*children, **kwargs)
        self.evidence_model = dict(name=name, evidence=list(evidence), result="")

    def on_focus(self):
        self.app.focused_plan_id = None
        self.app.focused_evidence = self.evidence_model
        self.app.update_details()

    def on_descendant_focus(self, event):
        self.on_focus()
