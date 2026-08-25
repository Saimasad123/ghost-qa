import pytest
from app.services.xaml_generator import XamlGenerator
import xml.etree.ElementTree as ET


class TestXamlGenerator:
    """Test the XAML generator for well-formedness and structure."""

    def test_zero_step_test_case(self):
        """0-step test case should produce no <ui:Sequence> elements."""
        generator = XamlGenerator()
        test_case = {
            "id": "TC-000",
            "title": "Zero Step Test",
            "steps": [],
            "expected_result": ""
        }
        xaml = generator.generate_xaml(test_case)
        
        # Verify no <ui:Sequence> elements exist by counting opening tags
        sequence_count = xaml.count('<ui:Sequence')
        assert sequence_count == 0, f"Expected 0 Sequence elements for 0-step test case, got {sequence_count}"

    def test_one_step_test_case(self):
        """1-step test case should produce exactly 1 <ui:Sequence> element."""
        generator = XamlGenerator()
        test_case = {
            "id": "TC-001",
            "title": "One Step Test",
            "steps": [
                {"action": "Click Button", "selector": "button[id='submit']", "value": "", "assertion": ""}
            ],
            "expected_result": ""
        }
        xaml = generator.generate_xaml(test_case)
        
        # Verify exactly 1 <ui:Sequence> element exists
        sequence_count = xaml.count('<ui:Sequence')
        assert sequence_count == 1, f"Expected 1 Sequence element, got {sequence_count}"

    def test_n_step_test_case(self):
        """N-step test case should produce exactly N <ui:Sequence> elements."""
        generator = XamlGenerator()
        steps = [
            {"action": f"Step {i}", "selector": f"elem{i}", "value": "", "assertion": ""}
            for i in range(5)
        ]
        test_case = {
            "id": "TC-005",
            "title": "Five Step Test",
            "steps": steps,
            "expected_result": ""
        }
        xaml = generator.generate_xaml(test_case)
        
        # Verify exactly 5 <ui:Sequence> elements exist
        sequence_count = xaml.count('<ui:Sequence')
        assert sequence_count == 5, f"Expected 5 Sequence elements, got {sequence_count}"

    def test_required_namespace_declaration(self):
        """Generated XAML should include xmlns:ui='http://schemas.uipath.com/workflow/activities'."""
        generator = XamlGenerator()
        test_case = {
            "id": "TC-001",
            "title": "Namespace Test",
            "steps": [],
            "expected_result": ""
        }
        xaml = generator.generate_xaml(test_case)
        
        # Verify the namespace is present
        assert 'xmlns:ui="http://schemas.uipath.com/workflow/activities"' in xaml, \
            "Expected xmlns:ui namespace declaration in XAML"

    def test_root_attributes(self):
        """Root Activity element should have correct attributes including DisplayName and IdRef."""
        generator = XamlGenerator()
        test_case = {
            "id": "TC-123",
            "title": "Attributes Test",
            "steps": [],
            "expected_result": ""
        }
        xaml = generator.generate_xaml(test_case)
        
        # Verify key attributes are present
        assert 'DisplayName="Test_TC-123"' in xaml, "Expected DisplayName attribute"
        assert 'sap2010:WorkflowViewState.IdRef="test_TC-123"' in xaml, "Expected IdRef attribute"

    def test_step_with_selector(self):
        """A step with selector should generate UiBrowser element."""
        generator = XamlGenerator()
        test_case = {
            "id": "TC-001",
            "title": "Selector Test",
            "steps": [
                {"action": "Click", "selector": "button[id='click']"}
            ],
            "expected_result": ""
        }
        xaml = generator.generate_xaml(test_case)
        
        # Verify UiBrowser element exists with correct selector
        assert '<ui:UiBrowser' in xaml, "Expected UiBrowser element"
        assert 'Selector="button[id=\'click\']"' in xaml, "Expected correct selector value"

    def test_step_with_value(self):
        """A step with value should generate TypeInto element."""
        generator = XamlGenerator()
        test_case = {
            "id": "TC-001",
            "title": "Type Into Test",
            "steps": [
                {"action": "Enter Text", "selector": "input[id='name']", "value": "John Doe"}
            ],
            "expected_result": ""
        }
        xaml = generator.generate_xaml(test_case)
        
        # Verify TypeInto element exists with correct text
        assert '<ui:TypeInto' in xaml, "Expected TypeInto element"
        assert 'Text="John Doe"' in xaml, "Expected correct text value"

    def test_step_with_assertion(self):
        """A step with assertion should generate VerifyExpression element."""
        generator = XamlGenerator()
        test_case = {
            "id": "TC-001",
            "title": "Assertion Test",
            "steps": [
                {"action": "Verify", "selector": "div[id='result']", "assertion": "visible"}
            ],
            "expected_result": ""
        }
        xaml = generator.generate_xaml(test_case)
        
        # Verify VerifyExpression element exists with correct expected value
        assert '<ui:VerifyExpression' in xaml, "Expected VerifyExpression element"
        assert 'Expected="visible"' in xaml, "Expected correct assertion value"

    def test_expected_result_verification(self):
        """Expected result should generate an Assert element."""
        generator = XamlGenerator()
        test_case = {
            "id": "TC-001",
            "title": "Expected Result Test",
            "steps": [],
            "expected_result": "Success"
        }
        xaml = generator.generate_xaml(test_case)
        
        # Verify Assert element exists with correct True value
        assert '<ui:Assert' in xaml, "Expected Assert element"
        assert 'True="Success"' in xaml, "Expected correct True value"

    def test_xaml_declaration(self):
        """Generated XAML should have proper XML declaration."""
        generator = XamlGenerator()
        test_case = {
            "id": "TC-001",
            "title": "Declaration Test",
            "steps": [],
            "expected_result": ""
        }
        xaml = generator.generate_xaml(test_case)
        
        assert xaml.startswith('<?xml'), "Expected XML declaration at start"
        assert 'version=' in xaml, "Expected version attribute"
        assert 'encoding=' in xaml, "Expected encoding attribute"
