'use client';

import { forwardRef, useId } from 'react';
import {
  COMMERCIAL_BASIS_VALUES,
  type CommercialBasisValue,
} from '@/lib/admin-catalogue/offer-schema';
import { COMMERCIAL_BASIS_HELP, COMMERCIAL_BASIS_LABELS } from '@/lib/admin-catalogue/offer-labels';
import { CHECK_BOX } from './offer-form-fields';

type PublishBasisChoiceProps = {
  /** The basis the editor has picked for this publish, or null: nothing is selected until they pick one. */
  value: CommercialBasisValue | null;
  onChange: (basis: CommercialBasisValue) => void;
};

/**
 * Asked before publishing a retailer's submission, whose stored basis is only a placeholder nobody chose. It never
 * names that placeholder: it says nobody has chosen, and lists the three bases with nothing selected. The group's
 * first radio takes the ref so a held Publish can send the editor here.
 */
export const PublishBasisChoice = forwardRef<HTMLInputElement, PublishBasisChoiceProps>(
  function PublishBasisChoice({ value, onChange }, firstRadioRef) {
    const id = useId();
    const labelId = `${id}-label`;
    const hintId = `${id}-hint`;
    return (
      <div className="space-y-2">
        <p id={labelId} className="text-sm font-medium">
          Commercial basis
        </p>
        <p id={hintId} className="text-sm text-muted-foreground">
          This listing came from a retailer, so nobody has chosen yet how it is paid for. Choose the
          commercial basis to publish it.
        </p>
        <div
          role="radiogroup"
          aria-labelledby={labelId}
          aria-describedby={hintId}
          aria-required="true"
          className="space-y-2"
        >
          {COMMERCIAL_BASIS_VALUES.map((basis, index) => (
            <label key={basis} className="flex min-h-11 items-start gap-3 text-sm">
              <input
                ref={index === 0 ? firstRadioRef : undefined}
                type="radio"
                name={`${id}-basis`}
                value={basis}
                checked={value === basis}
                className={`${CHECK_BOX} mt-0.5`}
                aria-labelledby={`${id}-${basis}-name`}
                aria-describedby={`${id}-${basis}-hint`}
                onChange={() => onChange(basis)}
              />
              <span>
                <span id={`${id}-${basis}-name`} className="block font-medium">
                  {COMMERCIAL_BASIS_LABELS[basis]}
                </span>
                <span id={`${id}-${basis}-hint`} className="block text-muted-foreground">
                  {COMMERCIAL_BASIS_HELP[basis]}
                </span>
              </span>
            </label>
          ))}
        </div>
      </div>
    );
  }
);
