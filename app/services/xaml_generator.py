import xml.etree.ElementTree as ET
from typing import Dict, Any


class XamlGenerator:
    """Generates well-formed UiPath test .xaml files from JSON test case definitions."""

    def generate_xaml(self, test_case: Dict[str, Any]) -> str:
        """
        Convert a test case JSON to a UiPath .xaml test file.
        
        Args:
            test_case: Dict with id, title, priority, steps, expected_result
        
        Returns:
            Well-formed XAML string
        """
        test_id = test_case.get("id", "Unknown")
        title = test_case.get("title", "Untitled")
        steps = test_case.get("steps", [])
        expected = test_case.get("expected_result", "")

        # Define namespace mapping
        ET.register_namespace("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006")
        ET.register_namespace("sad", "clr-namespace:System.Activities.Debugger;assembly=mscorlib")
        ET.register_namespace("sap", "http://schemas.microsoft.com/netfx/2009/xaml/activities/presentation")
        ET.register_namespace("sap2010", "http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation")
        ET.register_namespace("ui", "http://schemas.uipath.com/workflow/activities")

        # Root element
        root = ET.Element(
            "Activity",
            {
                "mc:Ignorable": "sad",
                "x:Class": "{x:Null}",
                "xmlns": "http://schemas.microsoft.com/netfx/2013/xaml/activities",
                "xmlns:mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
                "xmlns:sad": "clr-namespace:System.Activities.Debugger;assembly=mscorlib",
                "xmlns:sap2010": "http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation",
                "xmlns:ui": "http://schemas.uipath.com/workflow/activities",
                "xmlns:sap": "http://schemas.microsoft.com/netfx/2009/xaml/activities/presentation",
                "DisplayName": f"Test_{test_id}",
                "sap2010:WorkflowViewState.IdRef": f"test_{test_id}",
            }
        )

        # FlowStep
        flow_step = ET.SubElement(
            root,
            "ui:FlowStep",
            {"DisplayName": "Execute Test"}
        )

        # FlowStep.Activities (Sequence container)
        activities = ET.SubElement(flow_step, "FlowStep.Activities")

        # N × <ui:Sequence> elements
        for i, step in enumerate(steps, 1):
            action = step.get("action", "")
            selector = step.get("selector", "")
            value = step.get("value", "")
            assertion = step.get("assertion", "")

            seq = ET.SubElement(
                activities,
                "ui:Sequence",
                {"DisplayName": f"Step {i}: {action}"}
            )

            if selector:
                ET.SubElement(
                    seq,
                    "ui:UiBrowser",
                    {"Selector": selector, "DisplayName": "Target UI Element"}
                )
            if value:
                ET.SubElement(
                    seq,
                    "ui:TypeInto",
                    {"Target": selector, "Text": value, "DisplayName": "Enter Value"}
                )
            if assertion:
                ET.SubElement(
                    seq,
                    "ui:VerifyExpression",
                    {"Actual": selector, "Expected": assertion, "DisplayName": "Assert Result"}
                )

        # Final verification for expected_result
        if expected:
            ET.SubElement(
                activities,
                "ui:Assert",
                {"DisplayName": f"Verify: {expected}", "True": expected}
            )

        # Serialise with declaration
        xml_str = ET.tostring(root, encoding="unicode", xml_declaration=True)
        return xml_str

    def get_xaml_metadata(self, test_case: Dict[str, Any]) -> Dict[str, Any]:
        """Return metadata about the generated XAML for UiPath Test Cloud."""
        steps = test_case.get("steps", [])
        estimated_duration_ms = len(steps) * 5000
        return {
            "test_id": test_case.get("id"),
            "title": test_case.get("title"),
            "priority": test_case.get("priority"),
            "step_count": len(steps),
            "estimated_duration_ms": estimated_duration_ms,
            "target_environment": "default"
        }
