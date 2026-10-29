import { classifyEmail } from "../email/classifier";
import { emailDataset } from "../email/dataset";

export function runEvals() {
  const failures = emailDataset.flatMap((testCase) => {
    const actualCategory = classifyEmail(testCase.subject, testCase.body);
    return actualCategory === testCase.expectedCategory
      ? []
      : [{ id: testCase.id, expected: testCase.expectedCategory, actual: actualCategory }];
  });
  return {
    total: emailDataset.length,
    passed: emailDataset.length - failures.length,
    accuracy: emailDataset.length ? (emailDataset.length - failures.length) / emailDataset.length : 0,
    failures,
  };
}

if (require.main === module) {
  console.log(JSON.stringify(runEvals(), null, 2));
}
